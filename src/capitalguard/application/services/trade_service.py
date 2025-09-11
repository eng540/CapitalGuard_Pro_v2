# --- START OF FINAL, CORRECTED AND ROBUST FILE (V5): src/capitalguard/application/services/trade_service.py ---
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
    try: return int(user_id) if user_id is not None else None
    except (TypeError, ValueError): return None

class TradeService:
    _SYMBOLS_CACHE: set[str] = set()
    _SYMBOLS_CACHE_TS: float = 0.0
    _SYMBOLS_CACHE_TTL_SEC: int = 6 * 60 * 60

    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    def _ensure_symbols_cache(self) -> None:
        now = time.time()
        if self._SYMBOLS_CACHE and (now - self._SYMBOLS_CACHE_TS) < self._SYMBOLS_CACHE_TTL_SEC: return
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get("https://api.binance.com/api/v3/exchangeInfo")
                r.raise_for_status()
                data = r.json()
            symbols = {s["symbol"].upper() for s in data.get("symbols", []) if s.get("status") == "TRADING"}
            if symbols:
                self._SYMBOLS_CACHE, self._SYMBOLS_CACHE_TS = symbols, now
                log.info("Loaded %s Binance symbols into cache.", len(symbols))
        except Exception as e:
            log.exception("Failed to refresh Binance symbols: %s", e)

    def _validate_symbol_exists(self, asset: str) -> str:
        norm = asset.strip().upper()
        self._ensure_symbols_cache()
        if self._SYMBOLS_CACHE and norm not in self._SYMBOLS_CACHE:
            raise ValueError(f'Invalid symbol "{asset}". Not found on Binance.')
        return norm

    def _update_cards(self, rec: Recommendation, is_new_update: bool = False) -> None:
        """
        Updates all published cards for a recommendation and optionally sends a private
        notification to the analyst for direct actions they took.
        """
        published_messages = self.repo.get_published_messages(rec.id)
        
        if published_messages:
            log.info(f"Updating {len(published_messages)} cards for rec #{rec.id}...")
            public_keyboard = public_channel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
            for msg_meta in published_messages:
                try:
                    # ✅ FIX: Use the new, more explicit notifier method
                    self.notifier.edit_recommendation_card_by_ids(
                        channel_id=msg_meta.telegram_channel_id,
                        message_id=msg_meta.telegram_message_id,
                        rec=rec,
                        keyboard=public_keyboard
                    )
                except Exception as e:
                    log.warning("Failed to update card for rec #%s in channel %s: %s", rec.id, msg_meta.telegram_channel_id, e)
        
        uid = _parse_int_user_id(rec.user_id)
        if uid and is_new_update:
            try:
                analyst_keyboard = analyst_control_panel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
                self.notifier.send_private_message(
                    chat_id=uid, rec=rec, keyboard=analyst_keyboard,
                    text_header="✅ تم تحديث التوصية بنجاح:",
                )
            except Exception as e:
                log.debug("Failed to send private update message to user %s: %s", uid, e)

    def _validate_sl_vs_entry(self, side: str, entry: float, sl: float) -> None:
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl <= entry): raise ValueError("في صفقات الشراء (LONG)، يجب أن يكون وقف الخسارة ≤ سعر الدخول.")
        if side_upper == "SHORT" and not (sl >= entry): raise ValueError("في صفقات البيع (SHORT)، يجب أن يكون وقف الخسارة ≥ سعر الدخول.")

    def _validate_targets(self, side: str, entry: float, tps: List[float]) -> None:
        if not tps: raise ValueError("مطلوب على الأقل هدف واحد.")
        side_upper = side.upper()
        if side_upper == "LONG" and not all(tp > entry for tp in tps): raise ValueError("في صفقات الشراء، يجب أن تكون جميع الأهداف > سعر الدخول.")
        elif side_upper == "SHORT" and not all(tp < entry for tp in tps): raise ValueError("في صفقات البيع، يجب أن تكون جميع الأهداف < سعر الدخول.")

    def create_recommendation(self, **kwargs) -> Recommendation:
        """
        Creates and saves a recommendation. This function is now 'silent' and does not send notifications.
        """
        log.info("Saving recommendation: asset=%s side=%s user=%s", kwargs.get('asset'), kwargs.get('side'), kwargs.get('user_id'))
        asset = self._validate_symbol_exists(kwargs['asset'])
        order_type_enum = OrderType(kwargs['order_type'].upper())
        
        if order_type_enum == OrderType.MARKET:
            if kwargs.get('live_price') is None: raise ValueError("Live price required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, kwargs['live_price']
        else:
            status, final_entry = RecommendationStatus.PENDING, kwargs['entry']
            
        self._validate_sl_vs_entry(kwargs['side'], final_entry, kwargs['stop_loss'])
        self._validate_targets(kwargs['side'], final_entry, kwargs['targets'])
        
        rec = self.repo.add(Recommendation(
            asset=Symbol(asset), side=Side(kwargs['side']), entry=Price(final_entry),
            stop_loss=Price(kwargs['stop_loss']), targets=Targets(kwargs['targets']),
            order_type=order_type_enum, status=status, market=kwargs['market'],
            notes=kwargs.get('notes'), user_id=kwargs.get('user_id'),
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None,
        ))
        return rec

    def _load_user_linked_channels(self, uid_int: int, only_active: bool = True) -> List[Any]:
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(uid_int)
            return ChannelRepository(s).list_by_user(user.id, only_active=only_active) if user else []

    def publish_recommendation(self, rec_id: int, user_id: Optional[str], channel_ids: Optional[List[int]] = None) -> Tuple[Recommendation, Dict]:
        """
        Publishes a recommendation. This function is now 'silent' and does not send notifications.
        """
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        if rec.status == RecommendationStatus.CLOSED: raise ValueError("Cannot publish a closed recommendation.")
        
        uid_int = _parse_int_user_id(user_id or rec.user_id)
        report = {"success": [], "failed": []}
        if not uid_int:
            report["failed"].append({"channel_id": None, "reason": "USER_NOT_RESOLVED"})
            return rec, report
            
        channels = self._load_user_linked_channels(uid_int, only_active=True)
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
            updated_rec = self.repo.get(rec_id)
            return updated_rec, report
            
        return rec, report

    def activate_recommendation(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.PENDING: return None
        rec.activate()
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec) # Update public cards
        
        text = f"<b>✅ تفعيل #{rec.asset.value}</b>\nتم الدخول في صفقة {rec.side.value.upper()} عند سعر ~{rec.entry.value:g}."
        for msg in self.repo.get_published_messages(rec_id):
            try: self.notifier.post_notification_reply(msg.telegram_channel_id, msg.telegram_message_id, text)
            except Exception as e: log.warning("Failed to send activation reply for rec #%s: %s", rec_id, msg.telegram_channel_id, e)
        
        uid = _parse_int_user_id(rec.user_id)
        if uid: self.notifier.send_private_message(uid, updated_rec, text_header=f"🔥 توصيتك #{rec.id} مفعلة الآن!")
        return updated_rec
    
    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec) # Update public cards
        
        pnl = _pct(rec.entry.value, exit_price, rec.side.value)
        emoji, r_text = ("🏆", "ربح") if pnl >= 0 else ("💔", "خسارة")
        text = f"<b>{emoji} إغلاق صفقة #{rec.asset.value}</b>\nتم الإغلاق عند {exit_price:g} بنتيجة {r_text} <b>{pnl:+.2f}%</b>."
        for msg in self.repo.get_published_messages(rec_id):
            try: self.notifier.post_notification_reply(msg.telegram_channel_id, msg.telegram_message_id, text)
            except Exception as e: log.warning("Failed to send close reply for rec #%s: %s", rec_id, msg.telegram_channel_id, e)
        
        log.info("Rec #%s closed at price=%s", rec_id, exit_price)
        return updated_rec

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Recommendation not found or is closed.")
        self._validate_sl_vs_entry(rec.side.value, rec.entry.value, new_sl)
        is_be = (new_sl == rec.entry.value)
        note = ("\n- تم نقل الوقف إلى الدخول." if is_be else f"\n- تم تحديث الوقف إلى {new_sl}.")
        rec.stop_loss = Price(new_sl)
        rec.notes = (rec.notes or "") + note
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec, is_new_update=True) # Update all cards and notify analyst
        
        if is_be:
            text = f"<b>🛡️ تأمين صفقة #{rec.asset.value}</b>\nتم نقل وقف الخسارة إلى نقطة الدخول."
            for msg in self.repo.get_published_messages(rec_id):
                try: self.notifier.post_notification_reply(msg.telegram_channel_id, msg.telegram_message_id, text)
                except Exception as e: log.warning("Failed to send SL-to-BE reply for rec #%s: %s", rec_id, msg.telegram_channel_id, e)
        
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
        self._update_cards(updated_rec, is_new_update=True) # Update all cards and notify analyst
        log.info("Rec #%s partial close note added", rec.id)
        return updated_rec

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Recommendation not found or is closed.")
        self._validate_targets(rec.side.value, rec.entry.value, new_targets)
        rec.targets = Targets(new_targets)
        rec.notes = (rec.notes or "") + f"\n- تم تحديث الأهداف إلى [{', '.join(map(str, new_targets))}]."
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec, is_new_update=True) # Update all cards and notify analyst
        log.info("Rec #%s targets updated to [%s]", rec.id, ', '.join(map(str, new_targets)))
        return updated_rec
        
    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        return self.repo.get_recent_assets_for_user(user_id, limit)
# --- END OF FINAL, CORRECTED AND ROBUST FILE (V5) ---