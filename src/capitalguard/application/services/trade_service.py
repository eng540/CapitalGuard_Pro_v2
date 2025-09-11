# --- START OF FINAL, CORRECTED AND ROBUST FILE (V15): src/capitalguard/application/services/trade_service.py ---
import logging
import time
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import httpx

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger(__name__)

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try: return int(user_id) if user_id is not None else None
    except (TypeError, ValueError): return None

class TradeService:
    _SYMBOLS_CACHE: set[str] = set()
    _SYMBOLS_CACHE_TS: float = 0.0
    _SYMBOLS_CACHE_TTL_SEC: int = 6 * 60 * 60

    def __init__(self, repo: RecommendationRepository, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    # --- Validation Helpers ---
    def _ensure_symbols_cache(self) -> None:
        now = time.time()
        if self._SYMBOLS_CACHE and (now - self._SYMBOLS_CACHE_TS) < self._SYMBOLS_CACHE_TTL_SEC: return
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get("https://api.binance.com/api/v3/exchangeInfo")
                r.raise_for_status()
                data = r.json()
            symbols = {s["symbol"].upper() for s in data.get("symbols", []) if s.get("status") == "TRADING"}
            if symbols: self._SYMBOLS_CACHE, self._SYMBOLS_CACHE_TS = symbols, now
        except Exception as e: log.exception("Failed to refresh Binance symbols: %s", e)

    def _validate_symbol_exists(self, asset: str) -> str:
        norm = asset.strip().upper()
        self._ensure_symbols_cache()
        if self._SYMBOLS_CACHE and norm not in self._SYMBOLS_CACHE:
            raise ValueError(f'Invalid symbol "{asset}". Not found on Binance.')
        return norm

    def _validate_sl_vs_entry(self, side: str, entry: float, sl: float) -> None:
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl <= entry): raise ValueError("For LONG trades, Stop Loss must be <= Entry Price.")
        if side_upper == "SHORT" and not (sl >= entry): raise ValueError("For SHORT trades, Stop Loss must be >= Entry Price.")

    def _validate_targets(self, side: str, entry: float, tps: List[float]) -> None:
        if not tps: raise ValueError("At least one target is required.")
        side_upper = side.upper()
        if side_upper == "LONG" and not all(tp > entry for tp in tps): raise ValueError("For LONG trades, all targets must be > Entry Price.")
        elif side_upper == "SHORT" and not all(tp < entry for tp in tps): raise ValueError("For SHORT trades, all targets must be < Entry Price.")

    # --- Core Business Logic ---

    def create_recommendation(self, **kwargs) -> Recommendation:
        asset = self._validate_symbol_exists(kwargs['asset'])
        order_type_enum = OrderType(kwargs['order_type'].upper())
        
        if order_type_enum == OrderType.MARKET:
            if kwargs.get('live_price') is None: raise ValueError("Live price required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, kwargs['live_price']
        else:
            status, final_entry = RecommendationStatus.PENDING, kwargs['entry']
            
        self._validate_sl_vs_entry(kwargs['side'], final_entry, kwargs['stop_loss'])
        self._validate_targets(kwargs['side'], final_entry, kwargs['targets'])
        
        rec = Recommendation(
            asset=Symbol(asset), side=Side(kwargs['side']), entry=Price(final_entry),
            stop_loss=Price(kwargs['stop_loss']), targets=Targets(kwargs['targets']),
            order_type=order_type_enum, status=status, market=kwargs['market'],
            notes=kwargs.get('notes'), user_id=kwargs.get('user_id'),
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None,
        )
        
        return self.repo.add_with_event(rec)

    def publish_recommendation(self, rec_id: int, user_id: Optional[str], channel_ids: Optional[List[int]] = None) -> Tuple[Recommendation, Dict]:
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        
        uid_int = _parse_int_user_id(user_id or rec.user_id)
        report = {"success": [], "failed": []}
        if not uid_int:
            report["failed"].append({"channel_id": None, "reason": "USER_NOT_RESOLVED"})
            return rec, report
            
        channels = self.repo._load_user_linked_channels(uid_int, only_active=True)
        if channel_ids: channels = [ch for ch in channels if ch.telegram_channel_id in set(channel_ids)]
        
        if not channels:
            return rec, report
            
        keyboard = public_channel_keyboard(rec.id)
        publications = []
        for ch in channels:
            try:
                res = self.notifier.post_to_channel(ch.telegram_channel_id, rec, keyboard)
                if res:
                    publications.append({"recommendation_id": rec.id, "telegram_channel_id": res[0], "telegram_message_id": res[1]})
                    report["success"].append({"channel_id": ch.telegram_channel_id, "message_id": res[1]})
                else: report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": "POST_FAILED"})
            except Exception as e:
                log.error("Failed to publish to channel %s: %s", ch.telegram_channel_id, e, exc_info=True)
                report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": str(e)})
                
        if publications:
            self.repo.save_published_messages(publications)
            self.repo.update_legacy_publication_fields(rec_id, publications[0])
            return self.repo.get(rec_id), report
            
        return rec, report

    def activate_recommendation(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.PENDING: return None
        
        rec.activate()
        rec.highest_price_reached = rec.entry.value
        rec.lowest_price_reached = rec.entry.value
        
        event_data = {"activated_at": rec.activated_at.isoformat()}
        return self.repo.update_with_event(rec, "ACTIVATED", event_data)

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        
        old_status = rec.status
        rec.close(exit_price)
        
        event_data = {
            "old_status": old_status.value,
            "exit_price": exit_price,
            "closed_at": rec.closed_at.isoformat()
        }
        return self.repo.update_with_event(rec, "CLOSED", event_data)

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Recommendation not found or is closed.")
        
        old_sl = rec.stop_loss.value
        self._validate_sl_vs_entry(rec.side.value, rec.entry.value, new_sl)
        
        rec.stop_loss = Price(new_sl)
        
        event_data = {"old_sl": old_sl, "new_sl": new_sl}
        return self.repo.update_with_event(rec, "SL_UPDATE", event_data)

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Recommendation not found or is closed.")
        
        old_targets = rec.targets.values
        self._validate_targets(rec.side.value, rec.entry.value, new_targets)
        
        rec.targets = Targets(new_targets)
        
        event_data = {"old_targets": old_targets, "new_targets": new_targets}
        return self.repo.update_with_event(rec, "TP_UPDATE", event_data)

    def take_partial_profit(self, rec_id: int, percentage: float, price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.ACTIVE:
            raise ValueError("Partial profit can only be taken on active recommendations.")
        
        event_data = {"percentage": percentage, "price": price}
        return self.repo.update_with_event(rec, "PARTIAL_PROFIT_TAKEN", event_data)

    def update_price_tracking(self, rec_id: int, current_price: float) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.ACTIVE: return None

        updated = False
        if rec.highest_price_reached is None or current_price > rec.highest_price_reached:
            rec.highest_price_reached = current_price
            updated = True
        if rec.lowest_price_reached is None or current_price < rec.lowest_price_reached:
            rec.lowest_price_reached = current_price
            updated = True
            
        if updated:
            return self.repo.update(rec)
        return None

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        return self.repo.get_recent_assets_for_user(user_id, limit)
# --- END OF FINAL, CORRECTED AND ROBUST FILE (V15) ---