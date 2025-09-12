# --- COMPLETE AND FINAL VERSION: src/capitalguard/application/services/trade_service.py ---
import logging
import time
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import httpx

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository, ChannelRepository
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
from capitalguard.interfaces.telegram.ui_texts import _pct

log = logging.getLogger(__name__)

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try: 
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError): 
        return None

class TradeService:
    _SYMBOLS_CACHE: set[str] = set()
    _SYMBOLS_CACHE_TS: float = 0.0
    _SYMBOLS_CACHE_TTL_SEC: int = 6 * 60 * 60

    def __init__(self, repo: RecommendationRepository, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    def _load_user_linked_channels(self, uid_int: int, only_active: bool = True) -> List[Any]:
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(uid_int)
            if not user: 
                return []
            return ChannelRepository(s).list_by_user(user.id, only_active=only_active)

    def _notify_all_channels(self, rec_id: int, text: str):
        published_messages = self.repo.get_published_messages(rec_id)
        for msg_meta in published_messages:
            try:
                self.notifier.post_notification_reply(
                    chat_id=msg_meta.telegram_channel_id, 
                    message_id=msg_meta.telegram_message_id, 
                    text=text
                )
            except Exception as e:
                log.warning("Failed to send reply notification for rec #%s to channel %s: %s", 
                           rec_id, msg_meta.telegram_channel_id, e)

    def _update_all_cards(self, rec: Recommendation):
        published_messages = self.repo.get_published_messages(rec.id)
        if not published_messages: 
            return
        
        log.info(f"Updating {len(published_messages)} cards for rec #{rec.id}...")
        keyboard = public_channel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
        for msg_meta in published_messages:
            try:
                self.notifier.edit_recommendation_card_by_ids(
                    channel_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    rec=rec,
                    keyboard=keyboard
                )
            except Exception as e:
                log.warning("Failed to update card for rec #%s in channel %s: %s", 
                           rec.id, msg_meta.telegram_channel_id, e)

    # --- Validation Helpers ---
    def _ensure_symbols_cache(self) -> None:
        now = time.time()
        if self._SYMBOLS_CACHE and (now - self._SYMBOLS_CACHE_TS) < self._SYMBOLS_CACHE_TTL_SEC: 
            return
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get("https://api.binance.com/api/v3/exchangeInfo")
                r.raise_for_status()
                data = r.json()
            symbols = {s["symbol"].upper() for s in data.get("symbols", []) if s.get("status") == "TRADING"}
            if symbols: 
                self._SYMBOLS_CACHE, self._SYMBOLS_CACHE_TS = symbols, now
        except Exception as e: 
            log.exception("Failed to refresh Binance symbols: %s", e)

    def _validate_symbol_exists(self, asset: str) -> str:
        norm = asset.strip().upper()
        self._ensure_symbols_cache()
        if self._SYMBOLS_CACHE and norm not in self._SYMBOLS_CACHE:
            raise ValueError(f'Invalid symbol "{asset}". Not found on Binance.')
        return norm

    def _validate_sl_vs_entry(self, side: str, entry: float, sl: float) -> None:
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl <= entry): 
            raise ValueError("For LONG trades, Stop Loss must be <= Entry Price.")
        if side_upper == "SHORT" and not (sl >= entry): 
            raise ValueError("For SHORT trades, Stop Loss must be >= Entry Price.")

    # ✅ --- START: FIX for Target Sorting ---
    def _validate_and_sort_targets(self, side: str, entry: float, tps: List[float]) -> List[float]:
        """Validates targets and sorts them logically based on the trade side."""
        if not tps: 
            raise ValueError("At least one target is required.")
        
        side_upper = side.upper()
        if side_upper == "LONG":
            if not all(tp > entry for tp in tps): 
                raise ValueError("For LONG trades, all targets must be > Entry Price.")
            return sorted(tps)  # Sort ascending for LONG
        elif side_upper == "SHORT":
            if not all(tp < entry for tp in tps): 
                raise ValueError("For SHORT trades, all targets must be < Entry Price.")
            return sorted(tps, reverse=True)  # Sort descending for SHORT
        else:
            raise ValueError("Invalid trade side.")
    # ✅ --- END: FIX for Target Sorting ---

    # --- Core Business Logic ---

    def create_recommendation(self, **kwargs) -> Recommendation:
        asset = self._validate_symbol_exists(kwargs['asset'])
        order_type_enum = OrderType(kwargs['order_type'].upper())
        
        if order_type_enum == OrderType.MARKET:
            if kwargs.get('live_price') is None: 
                raise ValueError("Live price required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, kwargs['live_price']
        else:
            status, final_entry = RecommendationStatus.PENDING, kwargs['entry']
            
        self._validate_sl_vs_entry(kwargs['side'], final_entry, kwargs['stop_loss'])
        
        # ✅ Use the new sorting and validation function
        sorted_targets = self._validate_and_sort_targets(kwargs['side'], final_entry, kwargs['targets'])
        
        rec = Recommendation(
            asset=Symbol(asset), 
            side=Side(kwargs['side']), 
            entry=Price(final_entry),
            stop_loss=Price(kwargs['stop_loss']), 
            targets=Targets(sorted_targets),
            order_type=order_type_enum, 
            status=status, 
            market=kwargs['market'],
            notes=kwargs.get('notes'), 
            user_id=kwargs.get('user_id'),
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None,
        )
        
        return self.repo.add_with_event(rec)

    def publish_recommendation(self, rec_id: int, user_id: Optional[str], channel_ids: Optional[List[int]] = None) -> Tuple[Recommendation, Dict]:
        rec = self.repo.get(rec_id)
        if not rec: 
            raise ValueError(f"Recommendation {rec_id} not found.")
        
        uid_int = _parse_int_user_id(user_id or rec.user_id)
        report = {"success": [], "failed": []}
        if not uid_int:
            report["failed"].append({"channel_id": None, "reason": "USER_NOT_RESOLVED"})
            return rec, report
            
        channels = self._load_user_linked_channels(uid_int, only_active=True)
        if channel_ids: 
            channels = [ch for ch in channels if ch.telegram_channel_id in set(channel_ids)]
        
        if not channels: 
            return rec, report
            
        keyboard = public_channel_keyboard(rec.id)
        
        for ch in channels:
            try:
                res = self.notifier.post_to_channel(ch.telegram_channel_id, rec, keyboard)
                if res:
                    publication_data = [{
                        "recommendation_id": rec.id, 
                        "telegram_channel_id": res[0], 
                        "telegram_message_id": res[1]
                    }]
                    self.repo.save_published_messages(publication_data)
                    report["success"].append({
                        "channel_id": ch.telegram_channel_id, 
                        "message_id": res[1]
                    })
                else: 
                    report["failed"].append({
                        "channel_id": ch.telegram_channel_id, 
                        "reason": "POST_FAILED"
                    })
            except Exception as e:
                log.error("Failed to publish to channel %s: %s", ch.telegram_channel_id, e, exc_info=True)
                report["failed"].append({
                    "channel_id": ch.telegram_channel_id, 
                    "reason": str(e)
                })
                
        if report["success"]:
            first_pub = report["success"][0]
            self.repo.update_legacy_publication_fields(rec_id, {
                'telegram_channel_id': first_pub['channel_id'], 
                'telegram_message_id': first_pub['message_id']
            })
            
        return self.repo.get(rec_id), report

    def activate_recommendation(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.PENDING: 
            return None
        
        rec.activate()
        rec.highest_price_reached = rec.entry.value
        rec.lowest_price_reached = rec.entry.value
        
        updated_rec = self.repo.update_with_event(rec, "ACTIVATED", {
            "activated_at": rec.activated_at.isoformat()
        })
        
        self._update_all_cards(updated_rec)
        notification_text = f"<b>✅ تفعيل #{updated_rec.asset.value}</b>\nتم الدخول في صفقة {updated_rec.side.value.upper()} عند سعر ~{updated_rec.entry.value:g}."
        self._notify_all_channels(rec_id, notification_text)
        
        return updated_rec
    
    def close(self, rec_id: int, exit_price: float, reason: str = "MANUAL_CLOSE") -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec: 
            raise ValueError(f"Recommendation {rec_id} not found.")
        
        old_status = rec.status
        rec.close(exit_price)
        
        updated_rec = self.repo.update_with_event(rec, "CLOSED", {
            "old_status": old_status.value, 
            "exit_price": exit_price, 
            "closed_at": rec.closed_at.isoformat(), 
            "reason": reason
        })
        
        # ✅ --- START: FIX for Final TP Notification ---
        # If the reason for closing is hitting the final TP, send the TP hit notification first.
        if reason == "FINAL_TP_HIT" and updated_rec.targets.values:
            last_tp = updated_rec.targets.values[-1]
            tp_count = len(updated_rec.targets.values)
            tp_notification = f"<b>🔥 الهدف #{tp_count} (الأخير) تحقق لـ #{updated_rec.asset.value}!</b>\nالسعر وصل إلى {last_tp:g}."
            self._notify_all_channels(rec_id, tp_notification)
            time.sleep(0.5)  # Small delay to ensure notification order
        # ✅ --- END: FIX for Final TP Notification ---

        self._update_all_cards(updated_rec)
        pnl = _pct(rec.entry.value, exit_price, rec.side.value)
        emoji, r_text = ("🏆", "ربح") if pnl >= 0 else ("💔", "خسارة")
        close_notification = f"<b>{emoji} إغلاق صفقة #{updated_rec.asset.value}</b>\nتم الإغلاق عند {exit_price:g} بنتيجة {r_text} <b>{pnl:+.2f}%</b>."
        self._notify_all_channels(rec_id, close_notification)
        
        return updated_rec

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: 
            raise ValueError("Recommendation not found or is closed.")
        
        old_sl = rec.stop_loss.value
        self._validate_sl_vs_entry(rec.side.value, rec.entry.value, new_sl)
        rec.stop_loss = Price(new_sl)
        
        updated_rec = self.repo.update_with_event(rec, "SL_UPDATE", {
            "old_sl": old_sl, 
            "new_sl": new_sl
        })
        
        self._update_all_cards(updated_rec)
        is_be = (new_sl == rec.entry.value)
        if is_be:
            notification_text = f"<b>🛡️ تأمين صفقة #{updated_rec.asset.value}</b>\nتم نقل وقف الخسارة إلى نقطة الدخول."
        else:
            notification_text = f"<b>🛑 تحديث وقف الخسارة لـ #{updated_rec.asset.value}</b>\nوقف الخسارة الجديد هو {new_sl:g}."
        self._notify_all_channels(rec_id, notification_text)
        
        return updated_rec

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: 
            raise ValueError("Recommendation not found or is closed.")
        
        old_targets = rec.targets.values
        # ✅ Use the new sorting and validation function
        sorted_targets = self._validate_and_sort_targets(rec.side.value, rec.entry.value, new_targets)
        rec.targets = Targets(sorted_targets)
        
        updated_rec = self.repo.update_with_event(rec, "TP_UPDATE", {
            "old_targets": old_targets, 
            "new_targets": sorted_targets
        })
        
        self._update_all_cards(updated_rec)
        targets_str = ", ".join(map(lambda p: f"{p:g}", sorted_targets))
        notification_text = f"<b>🎯 تحديث الأهداف لـ #{updated_rec.asset.value}</b>\nالأهداف الجديدة هي: [{targets_str}]."
        self._notify_all_channels(rec_id, notification_text)

        return updated_rec

    def take_partial_profit(self, rec_id: int, percentage: float, price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.ACTIVE:
            raise ValueError("Partial profit can only be taken on active recommendations.")
        
        updated_rec = self.repo.update_with_event(rec, "PARTIAL_PROFIT_TAKEN", {
            "percentage": percentage, 
            "price": price
        })
        
        # ✅ Send notification for partial profit
        self._update_all_cards(updated_rec)
        pnl = _pct(rec.entry.value, price, rec.side.value)
        notification_text = f"<b>💰 جني أرباح جزئي لـ #{updated_rec.asset.value}</b>\nتم جني {percentage}% من الصفقة عند السعر {price:g} بربح {pnl:+.2f}%."
        self._notify_all_channels(rec_id, notification_text)
        
        return updated_rec

    def update_price_tracking(self, rec_id: int, current_price: float) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.ACTIVE: 
            return None
        
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
# --- END OF COMPLETE AND FINAL VERSION ---