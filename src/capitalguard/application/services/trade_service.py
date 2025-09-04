# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
import logging
import time
from typing import List, Optional
from datetime import datetime, timezone
import httpx

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.interfaces.telegram.keyboards import (
    public_channel_keyboard,
    analyst_control_panel_keyboard,
)

log = logging.getLogger(__name__)

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """Safely parse a user_id string to int, or return None if invalid."""
    try:
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return None

class TradeService:
    # ------------------------------
    # Symbol validation cache (Binance spot)
    # ------------------------------
    _SYMBOLS_CACHE: set[str] = set()
    _SYMBOLS_CACHE_TS: float = 0.0
    _SYMBOLS_CACHE_TTL_SEC: int = 6 * 60 * 60  # 6 hours

    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    def _ensure_symbols_cache(self) -> None:
        """Fetch & cache Binance symbols (spot) if cache is empty/expired."""
        now = time.time()
        if self._SYMBOLS_CACHE and (now - self._SYMBOLS_CACHE_TS) < self._SYMBOLS_CACHE_TTL_SEC:
            return
        try:
            url = "https://api.binance.com/api/v3/exchangeInfo"
            with httpx.Client(timeout=10) as client:
                r = client.get(url)
                r.raise_for_status()
                data = r.json()
            symbols = {
                s["symbol"].upper()
                for s in data.get("symbols", [])
                if s.get("status") == "TRADING"
            }
            if symbols:
                self._SYMBOLS_CACHE = symbols
                self._SYMBOLS_CACHE_TS = now
                log.info("Loaded %s Binance symbols into cache.", len(symbols))
            else:
                log.warning("exchangeInfo returned empty symbol list; keeping previous cache.")
        except Exception as e:
            log.exception("Failed to refresh Binance symbols: %s", e)

    def _validate_symbol_exists(self, asset: str) -> str:
        """
        Normalize + validate that asset exists on Binance (spot);
        raises ValueError otherwise. Returns normalized symbol (uppercased).
        """
        norm = asset.strip().upper()
        self._ensure_symbols_cache()
        if self._SYMBOLS_CACHE and norm not in self._SYMBOLS_CACHE:
            raise ValueError(
                f'Invalid symbol "{asset}". Not found on Binance (spot). '
                "Use a valid trading pair like BTCUSDT, ETHUSDT, etc."
            )
        return norm

    def _update_cards(self, rec: Recommendation) -> None:
        """Private helper to update public and private cards after a change."""
        public_keyboard = public_channel_keyboard(rec.id)
        self.notifier.edit_recommendation_card(rec, keyboard=public_keyboard)

        uid = _parse_int_user_id(rec.user_id)
        if uid is not None:
            analyst_keyboard = analyst_control_panel_keyboard(rec.id)
            self.notifier.send_private_message(
                chat_id=uid,
                rec=rec,
                keyboard=analyst_keyboard,
                text_header="✅ Recommendation updated successfully:",
            )

    def _validate_sl_vs_entry(self, side: str, entry: float, sl: float) -> None:
        """Validates that stop loss is logical compared to entry price."""
        side_upper = side.upper()
        # Allow SL == entry (break-even)
        if side_upper == "LONG" and not (sl <= entry):
            raise ValueError("For LONG trades, Stop Loss must be less than or equal to the Entry price.")
        if side_upper == "SHORT" and not (sl >= entry):
            raise ValueError("For SHORT trades, Stop Loss must be greater than or equal to the Entry price.")

    def _validate_targets(self, side: str, entry: float, tps: List[float]) -> None:
        """Validates that targets are logical compared to entry price."""
        if not tps:
            raise ValueError("At least one target price is required.")
        side_upper = side.upper()
        if side_upper == "LONG":
            if not all(tp > entry for tp in tps):
                raise ValueError("For LONG trades, all targets must be greater than the Entry price.")
        elif side_upper == "SHORT":
            if not all(tp < entry for tp in tps):
                raise ValueError("For SHORT trades, all targets must be less than the Entry price.")

    def create_and_publish_recommendation(
        self,
        asset: str,
        side: str,
        market: str,
        entry: float,
        stop_loss: float,
        targets: List[float],
        notes: Optional[str],
        user_id: Optional[str],
        order_type: str,
        live_price: Optional[float] = None,
    ) -> Recommendation:
        """The main business logic for creating and publishing a new recommendation."""
        log.info(
            "Creating recommendation: asset=%s side=%s order_type=%s user=%s",
            asset, side, order_type, user_id
        )

        # Normalize + validate symbol against Binance spot markets
        asset = self._validate_symbol_exists(asset)

        try:
            order_type_enum = OrderType(order_type.upper())
        except ValueError:
            valid = ", ".join(ot.value for ot in OrderType)
            raise ValueError(f"Invalid order_type: {order_type}. Must be one of {valid}")

        if order_type_enum == OrderType.MARKET:
            if live_price is None:
                raise ValueError("Live price is required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, live_price
        else:
            status, final_entry = RecommendationStatus.PENDING, entry

        self._validate_sl_vs_entry(side, final_entry, stop_loss)
        self._validate_targets(side, final_entry, targets)

        rec_to_save = Recommendation(
            asset=Symbol(asset),
            side=Side(side),
            entry=Price(final_entry),
            stop_loss=Price(stop_loss),
            targets=Targets(targets),
            order_type=order_type_enum,
            status=status,
            market=market,
            notes=notes,
            user_id=user_id,
        )
        if rec_to_save.status == RecommendationStatus.ACTIVE:
            rec_to_save.activated_at = datetime.now(timezone.utc)

        saved_rec = self.repo.add(rec_to_save)

        # Publish to public channel
        public_keyboard = public_channel_keyboard(saved_rec.id)
        posted_location = self.notifier.post_recommendation_card(saved_rec, keyboard=public_keyboard)
        if posted_location:
            channel_id, message_id = posted_location
            # Persist location + published_at
            saved_rec.channel_id = channel_id
            saved_rec.message_id = message_id
            saved_rec.published_at = datetime.now(timezone.utc)
            self.repo.update(saved_rec)
        else:
            self.notifier.send_admin_alert(f"Failed to publish rec #{saved_rec.id} to channel.")

        # DM analyst with control panel (if we have a valid int chat id)
        uid = _parse_int_user_id(user_id)
        if uid is not None:
            analyst_keyboard = analyst_control_panel_keyboard(saved_rec.id)
            self.notifier.send_private_message(
                chat_id=uid,
                rec=saved_rec,
                keyboard=analyst_keyboard,
                text_header="🚀 Published! Here is your private control panel:",
            )

        return saved_rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")

        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info("Rec #%s closed at price=%s (status=%s)", rec_id, exit_price, updated_rec.status.value)
        return updated_rec

    def list_open(self, symbol: Optional[str] = None) -> List[Recommendation]:
        return self.repo.list_open(symbol=symbol)

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        return self.repo.list_all(symbol=symbol, status=status)

    def move_sl_to_be(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            return None
        # Use update_sl to keep validation & card updates consistent
        return self.update_sl(rec_id, rec.entry.value)

    def add_partial_close_note(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            return None
        note = f"\n- 50% of position closed on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC."
        rec.notes = (rec.notes or "") + note
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info("Rec #%s partial close note added", rec_id)
        return updated_rec

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError("Recommendation not found or is closed.")

        # Validation (supports BE)
        self._validate_sl_vs_entry(rec.side.value, rec.entry.value, new_sl)

        rec.stop_loss = Price(new_sl)
        note_text = "\n- SL moved to BE." if new_sl == rec.entry.value else f"\n- SL updated to {new_sl}."
        rec.notes = (rec.notes or "") + note_text
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info("Rec #%s SL updated to %s", rec_id, new_sl)
        return updated_rec

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError("Recommendation not found or is closed.")
        self._validate_targets(rec.side.value, rec.entry.value, new_targets)
        rec.targets = Targets(new_targets)
        targets_str = ", ".join(map(str, new_targets))
        rec.notes = (rec.notes or "") + f"\n- TPs updated to [{targets_str}]."
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info("Rec #%s targets updated to [%s]", rec_id, targets_str)
        return updated_rec

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        """Pass-through method to get recent assets from the repository."""
        return self.repo.get_recent_assets_for_user(user_id, limit)
# --- END OF FILE ---