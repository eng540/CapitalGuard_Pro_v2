# --- START OF COMPLETE MODIFIED FILE: src/capitalguard/application/services/trade_service.py ---
import logging
import time
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import httpx

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.interfaces.telegram.keyboards import (
    public_channel_keyboard,
    analyst_control_panel_keyboard,
)
from capitalguard.interfaces.telegram.ui_texts import _pct

from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

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

    # -------- Symbol validation helpers --------
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

    # -------- UI card updates --------
    def _update_cards(self, rec: Recommendation) -> None:
        """
        Updates ALL published cards for a recommendation and the private analyst panel.
        """
        published_messages = self.repo.get_published_messages(rec.id)
        
        if published_messages:
            log.info(f"Updating {len(published_messages)} cards for rec #{rec.id}...")
            # If the recommendation is closed, remove the keyboard
            public_keyboard = public_channel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
            for msg_meta in published_messages:
                # To use the existing notifier method, we temporarily set the IDs on a copy of the object
                temp_rec = rec
                temp_rec.channel_id = msg_meta.telegram_channel_id
                temp_rec.message_id = msg_meta.telegram_message_id
                try:
                    self.notifier.edit_recommendation_card(temp_rec, keyboard=public_keyboard)
                except Exception as e:
                    log.warning(
                        "Failed to update card for rec #%s in channel %s (msg %s): %s",
                        rec.id, msg_meta.telegram_channel_id, msg_meta.telegram_message_id, e
                    )
        
        uid = _parse_int_user_id(rec.user_id)
        if uid is not None:
            try:
                # If the recommendation is closed, remove the keyboard from the private panel too
                analyst_keyboard = analyst_control_panel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
                self.notifier.send_private_message(
                    chat_id=uid,
                    rec=rec,
                    keyboard=analyst_keyboard,
                    text_header="✅ تم تحديث التوصية بنجاح:",
                )
            except Exception as e:
                log.debug("Failed to send private update message to user %s: %s", uid, e)

    # -------- Validation helpers --------
    def _validate_sl_vs_entry(self, side: str, entry: float, sl: float) -> None:
        """Validates that stop loss is logical compared to entry price."""
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl <= entry):
            raise ValueError("في صفقات الشراء (LONG)، يجب أن يكون وقف الخسارة ≤ سعر الدخول.")
        if side_upper == "SHORT" and not (sl >= entry):
            raise ValueError("في صفقات البيع (SHORT)، يجب أن يكون وقف الخسارة ≥ سعر الدخول.")

    def _validate_targets(self, side: str, entry: float, tps: List[float]) -> None:
        """Validates that targets are logical compared to entry price."""
        if not tps:
            raise ValueError("مطلوب على الأقل هدف واحد.")
        side_upper = side.upper()
        if side_upper == "LONG":
            if not all(tp > entry for tp in tps):
                raise ValueError("في صفقات الشراء، يجب أن تكون جميع الأهداف > سعر الدخول.")
        elif side_upper == "SHORT":
            if not all(tp < entry for tp in tps):
                raise ValueError("في صفقات البيع، يجب أن تكون جميع الأهداف < سعر الدخول.")

    # =========================
    # Core save/publish actions
    # =========================
    def create_recommendation(
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
        """
        Saves a recommendation without publishing.
        """
        log.info("Saving recommendation: asset=%s side=%s user=%s", asset, side, user_id)
        asset = self._validate_symbol_exists(asset)
        order_type_enum = OrderType(order_type.upper())

        if order_type_enum == OrderType.MARKET:
            if live_price is None: raise ValueError("Live price required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, live_price
        else:
            status, final_entry = RecommendationStatus.PENDING, entry

        self._validate_sl_vs_entry(side, final_entry, stop_loss)
        self._validate_targets(side, final_entry, targets)

        rec = self.repo.add(Recommendation(
            asset=Symbol(asset), side=Side(side), entry=Price(final_entry),
            stop_loss=Price(stop_loss), targets=Targets(targets),
            order_type=order_type_enum, status=status, market=market,
            notes=notes, user_id=user_id,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None,
        ))
        
        uid = _parse_int_user_id(user_id)
        if uid:
            self.notifier.send_private_message(
                chat_id=uid, rec=rec, keyboard=analyst_control_panel_keyboard(rec.id),
                text_header="💾 تم حفظ التوصية بنجاح (بدون نشر)."
            )
        return rec

    def _load_user_linked_channels(self, uid_int: int, only_active: bool = True) -> List[Any]:
        """Returns a list of linked channel ORM rows for a user."""
        with SessionLocal() as s:
            user_repo, channel_repo = UserRepository(s), ChannelRepository(s)
            user = user_repo.find_by_telegram_id(uid_int)
            return channel_repo.list_by_user(user.id, only_active=only_active) if user else []

    def publish_recommendation(
        self, rec_id: int, user_id: Optional[str], channel_ids: Optional[List[int]] = None
    ) -> Tuple[Recommendation, Dict]:
        """Publishes a recommendation and records EVERY successful publication."""
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        if rec.status == RecommendationStatus.CLOSED: raise ValueError("Cannot publish a closed recommendation.")

        uid_int = _parse_int_user_id(user_id or rec.user_id)
        report = {"success": [], "failed": []}
        if not uid_int:
            report["failed"].append({"channel_id": None, "reason": "USER_NOT_RESOLVED"})
            return rec, report

        channels = self._load_user_linked_channels(uid_int, only_active=True)
        if channel_ids:
            channels = [ch for ch in channels if ch.telegram_channel_id in set(channel_ids)]

        if not channels:
            self.notifier.send_private_message(
                chat_id=uid_int, rec=rec, keyboard=analyst_control_panel_keyboard(rec.id),
                text_header="ℹ️ لا توجد قنوات مرتبطة لنشر التوصية."
            )
            return rec, report

        keyboard = public_channel_keyboard(rec.id)
        publications = []
        for ch in channels:
            try:
                res = self.notifier.post_to_channel(ch.telegram_channel_id, rec, keyboard)
                if res:
                    publications.append({"recommendation_id": rec.id, "telegram_channel_id": res[0], "telegram_message_id": res[1]})
                    report["success"].append({"channel_id": ch.telegram_channel_id, "message_id": res[1]})
                else:
                    report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": "POST_FAILED"})
            except Exception as e:
                log.error("Failed to publish to channel %s: %s", ch.telegram_channel_id, e, exc_info=True)
                report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": str(e)})

        if publications:
            self.repo.save_published_messages(publications)
            self.repo.update_legacy_publication_fields(rec_id, publications[0])
            updated_rec = self.repo.get(rec_id)
            self.notifier.send_private_message(chat_id=uid_int, rec=updated_rec, keyboard=analyst_control_panel_keyboard(rec.id), text_header="🚀 تم النشر بنجاح!")
            return updated_rec, report
        
        self.notifier.send_private_message(chat_id=uid_int, rec=rec, keyboard=analyst_control_panel_keyboard(rec.id), text_header="❌ تعذر النشر.")
        return rec, report

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
        channel_ids: Optional[List[int]] = None,
        publish: bool = True,
    ) -> Recommendation:
        """Flexible workflow: saves and optionally publishes."""
        saved = self.create_recommendation(
            asset=asset, side=side, market=market, entry=entry, stop_loss=stop_loss,
            targets=targets, notes=notes, user_id=user_id, order_type=order_type,
            live_price=live_price,
        )
        if not publish:
            return saved
        updated_rec, _ = self.publish_recommendation(
            rec_id=saved.id, user_id=user_id, channel_ids=channel_ids,
        )
        return updated_rec

    def publish_existing(self, rec_id: int, user_id: Optional[str], target_channel_ids: Optional[List[int]] = None) -> Tuple[Recommendation, Dict]:
        return self.publish_recommendation(rec_id=rec_id, user_id=user_id, channel_ids=target_channel_ids)

    # =========================
    # Other actions
    # =========================
    def activate_recommendation(self, rec_id: int) -> Optional[Recommendation]:
        """Activates a PENDING recommendation and notifies all channels."""
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.PENDING: return None

        rec.activate()
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec) # This now updates all cards
        
        text = f"<b>✅ تفعيل #{rec.asset.value}</b>\nتم الدخول في صفقة {rec.side.value.upper()} عند سعر ~{rec.entry.value:g}."
        for msg in self.repo.get_published_messages(rec_id):
            try:
                self.notifier.post_notification_reply(msg.telegram_channel_id, msg.telegram_message_id, text)
            except Exception as e:
                log.warning("Failed to send activation reply for rec #%s to channel %s: %s", rec_id, msg.telegram_channel_id, e)
        
        uid = _parse_int_user_id(rec.user_id)
        if uid: self.notifier.send_private_message(uid, updated_rec, text_header=f"🔥 توصيتك #{rec.id} مفعلة الآن!")
        return updated_rec
    
    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        """Closes a recommendation and notifies all channels."""
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")

        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        
        pnl = _pct(rec.entry.value, exit_price, rec.side.value)
        emoji = "🏆" if pnl >= 0 else "💔"; r_text = "ربح" if pnl >= 0 else "خسارة"
        text = f"<b>{emoji} إغلاق صفقة #{rec.asset.value}</b>\nتم الإغلاق عند {exit_price:g} بنتيجة {r_text} <b>{pnl:+.2f}%</b>."
        for msg in self.repo.get_published_messages(rec_id):
            try:
                self.notifier.post_notification_reply(msg.telegram_channel_id, msg.telegram_message_id, text)
            except Exception as e:
                log.warning("Failed to send close reply for rec #%s to channel %s: %s", rec_id, msg.telegram_channel_id, e)
        
        log.info("Rec #%s closed at price=%s", rec_id, exit_price)
        return updated_rec

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        """Updates the stop loss and notifies all channels if moved to Break-Even."""
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Recommendation not found or is closed.")
        self._validate_sl_vs_entry(rec.side.value, rec.entry.value, new_sl)
        
        is_be = (new_sl == rec.entry.value)
        note = ("\n- تم نقل الوقف إلى الدخول." if is_be else f"\n- تم تحديث الوقف إلى {new_sl}.")
        
        rec.stop_loss = Price(new_sl)
        rec.notes = (rec.notes or "") + note
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        
        if is_be:
            text = f"<b>🛡️ تأمين صفقة #{rec.asset.value}</b>\nتم نقل وقف الخسارة إلى نقطة الدخول."
            for msg in self.repo.get_published_messages(rec_id):
                try:
                    self.notifier.post_notification_reply(msg.telegram_channel_id, msg.telegram_message_id, text)
                except Exception as e:
                    log.warning("Failed to send SL-to-BE reply for rec #%s to channel %s: %s", rec_id, msg.telegram_channel_id, e)

        log.info("Rec #%s SL updated to %s", rec.id, new_sl)
        return updated_rec

    def move_sl_to_be(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: return None
        return self.update_sl(rec_id, rec.entry.value)

    def add_partial_close_note(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: return None
        note = f"\n- تم إغلاق 50% من الصفقة في {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC."
        rec.notes = (rec.notes or "") + note
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info("Rec #%s partial close note added", rec.id)
        return updated_rec

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Recommendation not found or is closed.")
        self._validate_targets(rec.side.value, rec.entry.value, new_targets)
        rec.targets = Targets(new_targets)
        rec.notes = (rec.notes or "") + f"\n- تم تحديث الأهداف إلى [{', '.join(map(str, new_targets))}]."
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info("Rec #%s targets updated to [%s]", rec.id, ', '.join(map(str, new_targets)))
        return updated_rec
        
    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        return self.repo.get_recent_assets_for_user(user_id, limit)
# --- END OF COMPLETE MODIFIED FILE ---