# --- START OF FINAL, COMPLETE, AND READY-TO-USE FILE ---
import logging
import time
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import httpx

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import NotifierPort
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository, ChannelRepository
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
from capitalguard.interfaces.telegram.ui_texts import _pct
from capitalguard.application.services.market_data_service import MarketDataService


log = logging.getLogger(__name__)

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try:
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return None


class TradeService:
    def __init__(self, repo: RecommendationRepository, notifier: NotifierPort, market_data_service: MarketDataService):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service

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
                log.warning(
                    "Failed to send reply notification for rec #%s to channel %s: %s",
                    rec_id, msg_meta.telegram_channel_id, e
                )

    def _update_all_cards(self, rec: Recommendation):
        published_messages = self.repo.get_published_messages(rec.id)
        if not published_messages:
            return

        log.info("Updating %d cards for rec #%s...", len(published_messages), rec.id)
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
                log.warning(
                    "Failed to update card for rec #%s in channel %s: %s",
                    rec.id, msg_meta.telegram_channel_id, e
                )

    def _validate_sl_vs_entry_on_create(self, side: str, entry: float, sl: float) -> None:
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl < entry):
            raise ValueError("For new LONG trades, Stop Loss must be < Entry Price.")
        if side_upper == "SHORT" and not (sl > entry):
            raise ValueError("For new SHORT trades, Stop Loss must be > Entry Price.")

    def _validate_and_sort_targets(self, side: str, entry: float, tps: List[float]) -> List[float]:
        if not tps:
            raise ValueError("At least one target is required.")
        side_upper = side.upper()
        if side_upper == "LONG":
            if not all(tp > entry for tp in tps):
                raise ValueError("For LONG trades, all targets must be > Entry Price.")
            return sorted(tps)
        elif side_upper == "SHORT":
            if not all(tp < entry for tp in tps):
                raise ValueError("For SHORT trades, all targets must be < Entry Price.")
            return sorted(tps, reverse=True)
        else:
            raise ValueError("Invalid trade side.")

    def create_recommendation(self, **kwargs) -> Recommendation:
        asset = kwargs['asset'].strip().upper()
        market = kwargs.get('market', 'Futures')
        if not self.market_data_service.is_valid_symbol(asset, market):
            raise ValueError(f"الرمز '{asset}' غير صالح أو غير متوفر في سوق '{market}'.")
        
        order_type_enum = OrderType(kwargs['order_type'].upper())
        if order_type_enum == OrderType.MARKET:
            if kwargs.get('live_price') is None:
                raise ValueError("Live price required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, kwargs['live_price']
        else:
            status, final_entry = RecommendationStatus.PENDING, kwargs['entry']

        self._validate_sl_vs_entry_on_create(kwargs['side'], final_entry, kwargs['stop_loss'])
        sorted_targets = self._validate_and_sort_targets(kwargs['side'], final_entry, kwargs['targets'])

        rec = Recommendation(
            asset=Symbol(asset),
            side=Side(kwargs['side']),
            entry=Price(final_entry),
            stop_loss=Price(kwargs['stop_loss']),
            targets=Targets(sorted_targets),
            order_type=order_type_enum,
            status=status,
            market=market,
            notes=kwargs.get('notes'),
            user_id=kwargs.get('user_id'),
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None,
            exit_strategy=kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP),
            profit_stop_price=kwargs.get('profit_stop_price'),
            open_size_percent=100.0
        )

        if rec.status == RecommendationStatus.ACTIVE:
            rec.highest_price_reached = rec.entry.value
            rec.lowest_price_reached = rec.entry.value

        return self.repo.add_with_event(rec)

    def publish_recommendation(
        self, rec_id: int, user_id: Optional[str], channel_ids: Optional[List[int]] = None
    ) -> Tuple[Recommendation, Dict[str, List[Dict[str, Any]]]]:
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        uid_int = _parse_int_user_id(user_id or rec.user_id)
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        if not uid_int:
            report["failed"].append({"channel_id": None, "reason": "USER_NOT_RESOLVED"})
            return rec, report
        channels = self._load_user_linked_channels(uid_int, only_active=True)
        if channel_ids:
            channels = [ch for ch in channels if ch.telegram_channel_id in set(channel_ids)]
        if not channels: return rec, report
        keyboard = public_channel_keyboard(rec.id)
        for ch in channels:
            try:
                res = self.notifier.post_to_channel(ch.telegram_channel_id, rec, keyboard)
                if res:
                    publication_data = [{"recommendation_id": rec.id, "telegram_channel_id": res[0], "telegram_message_id": res[1]}]
                    self.repo.save_published_messages(publication_data)
                    report["success"].append({"channel_id": ch.telegram_channel_id, "message_id": res[1]})
                else:
                    report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": "POST_FAILED"})
            except Exception as e:
                log.error("Failed to publish to channel %s: %s", ch.telegram_channel_id, e, exc_info=True)
                report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": str(e)})
        if report["success"]:
            first_pub = report["success"][0]
            self.repo.update_legacy_publication_fields(rec_id, {'telegram_channel_id': first_pub['channel_id'], 'telegram_message_id': first_pub['message_id']})
        return self.repo.get(rec_id), report

    def activate_recommendation(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.PENDING: return None
        rec.activate()
        rec.highest_price_reached = rec.entry.value
        rec.lowest_price_reached = rec.entry.value
        updated_rec = self.repo.update_with_event(rec, "ACTIVATED", {"activated_at": rec.activated_at.isoformat()})
        self._update_all_cards(updated_rec)
        notification_text = (f"<b>✅ تفعيل #{updated_rec.asset.value}</b>\n"
                           f"تم الدخول في صفقة {updated_rec.side.value.upper()} عند سعر ~{updated_rec.entry.value:g}.")
        self._notify_all_channels(rec_id, notification_text)
        return updated_rec

    def close(self, rec_id: int, exit_price: float, reason: str = "MANUAL_CLOSE") -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        rec.open_size_percent = 0.0
        old_status = rec.status
        rec.close(exit_price)
        pnl = _pct(rec.entry.value, exit_price, rec.side.value)
        if pnl > 0.001: close_status = "PROFIT"
        elif pnl < -0.001: close_status = "LOSS"
        else: close_status = "BREAKEVEN"
        updated_rec = self.repo.update_with_event(rec, "CLOSED", {"old_status": old_status.value, "exit_price": exit_price, "closed_at": rec.closed_at.isoformat(), "reason": reason, "close_status": close_status})
        if reason == "FINAL_TP_HIT" and updated_rec.targets.values:
            last_tp_price, _ = updated_rec.targets.values[-1].price, updated_rec.targets.values[-1].close_percent
            tp_count = len(updated_rec.targets.values)
            tp_notification = (f"<b>🔥 الهدف #{tp_count} (الأخير) تحقق لـ #{updated_rec.asset.value}!</b>\n"
                             f"السعر وصل إلى {last_tp_price:g}.")
            self._notify_all_channels(rec_id, tp_notification)
            time.sleep(0.5)
        self._update_all_cards(updated_rec)
        if close_status == "PROFIT": emoji, r_text = "🏆", "ربح"
        elif close_status == "LOSS": emoji, r_text = "💔", "خسارة"
        else: emoji, r_text = "🛡️", "تعادل"
        if close_status in ["PROFIT", "LOSS"]:
            close_notification = (f"<b>{emoji} إغلاق صفقة #{updated_rec.asset.value}</b>\n"
                                f"تم الإغلاق عند {exit_price:g} بنتيجة {r_text} <b>{pnl:+.2f}%</b>.")
        else:
            close_notification = (f"<b>{emoji} إغلاق صفقة #{updated_rec.asset.value}</b>\n"
                                f"تم الإغلاق عند نقطة الدخول (تعادل).")
        self._notify_all_channels(rec_id, close_notification)
        return updated_rec

    def take_partial_profit(self, rec_id: int, close_percent: float, price: float, triggered_by: str = "MANUAL") -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.ACTIVE:
            raise ValueError("Partial profit can only be taken on active recommendations.")
        if not (0 < close_percent <= rec.open_size_percent):
            raise ValueError(f"Invalid percentage. Must be between 0 and {rec.open_size_percent}.")
        rec.open_size_percent -= close_percent
        pnl_on_part = _pct(rec.entry.value, price, rec.side.value)
        event_type = "PARTIAL_PROFIT_AUTO" if triggered_by.upper() == "AUTO" else "PARTIAL_PROFIT_MANUAL"
        event_data = {"price": price, "closed_percent": close_percent, "remaining_percent": rec.open_size_percent, "pnl_on_part": pnl_on_part, "triggered_by": triggered_by}
        updated_rec = self.repo.update_with_event(rec, event_type, event_data)
        notification_text = (f"💰 <b>جني أرباح جزئي ({close_percent}%) لـ #{updated_rec.asset.value}</b>\n"
                           f"تم الإغلاق عند السعر {price:g} بربح {pnl_on_part:+.2f}%.")
        self._notify_all_channels(rec_id, notification_text)
        self._update_all_cards(updated_rec)
        if updated_rec.open_size_percent <= 0:
            log.info(f"Recommendation #{rec_id} fully closed via partial profits. Marking as closed.")
            reason = "AUTO_PARTIAL_FULL_CLOSE" if triggered_by.upper() == "AUTO" else "MANUAL_PARTIAL_FULL_CLOSE"
            return self.close(rec_id, price, reason=reason)
        return updated_rec

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Recommendation not found or is closed.")
        old_sl = rec.stop_loss.value
        rec.stop_loss = Price(new_sl)
        updated_rec = self.repo.update_with_event(rec, "SL_UPDATE", {"old_sl": old_sl, "new_sl": new_sl})
        self._update_all_cards(updated_rec)
        is_be = (new_sl == rec.entry.value)
        if is_be:
            notification_text = f"<b>🛡️ تأمين صفقة #{updated_rec.asset.value}</b>\nتم نقل وقف الخسارة إلى نقطة الدخول."
        else:
            notification_text = (f"<b>🛑 تحديث وقف الخسارة لـ #{updated_rec.asset.value}</b>\n"
                               f"وقف الخسارة الجديد هو {new_sl:g}.")
        self._notify_all_channels(rec_id, notification_text)
        return updated_rec

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Recommendation not found or is closed.")
        old_targets = [t.price for t in rec.targets.values]
        sorted_targets = self._validate_and_sort_targets(rec.side.value, rec.entry.value, new_targets)
        rec.targets = Targets(sorted_targets)
        updated_rec = self.repo.update_with_event(rec, "TP_UPDATE", {"old_targets": old_targets, "new_targets": sorted_targets})
        self._update_all_cards(updated_rec)
        targets_str = ", ".join(map(lambda p: f"{p:g}", sorted_targets))
        notification_text = (f"<b>🎯 تحديث الأهداف لـ #{updated_rec.asset.value}</b>\n"
                           f"الأهداف الجديدة هي: [{targets_str}].")
        self._notify_all_channels(rec_id, notification_text)
        return updated_rec

    def update_profit_stop(self, rec_id: int, new_profit_stop: Optional[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.ACTIVE: raise ValueError("Profit Stop can only be set on active recommendations.")
        old_price = rec.profit_stop_price
        if new_profit_stop is not None:
            if (rec.side.value == "LONG" and new_profit_stop <= rec.entry.value) or \
               (rec.side.value == "SHORT" and new_profit_stop >= rec.entry.value):
                raise ValueError("Profit Stop price must be in the profit zone.")
        rec.profit_stop_price = new_profit_stop
        updated_rec = self.repo.update_with_event(rec, "PROFIT_STOP_SET", {"old_price": old_price, "new_price": new_profit_stop})
        self._update_all_cards(updated_rec)
        if new_profit_stop is not None:
            notification_text = f"<b>🛡️ تم وضع وقف ربح لـ #{updated_rec.asset.value}</b> عند السعر {new_profit_stop:g}."
        else:
            notification_text = f"<b>🗑️ تم إزالة وقف الربح لـ #{updated_rec.asset.value}</b>."
        self._notify_all_channels(rec_id, notification_text)
        return updated_rec

    def update_exit_strategy(self, rec_id: int, new_strategy: ExitStrategy) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: raise ValueError("Cannot change strategy for a closed recommendation.")
        old_strategy = rec.exit_strategy
        rec.exit_strategy = new_strategy
        updated_rec = self.repo.update_with_event(rec, "STRATEGY_UPDATE", {"old_strategy": old_strategy.value, "new_strategy": new_strategy.value})
        self._update_all_cards(updated_rec)
        strategy_text = ("الإغلاق الآلي عند الهدف الأخير" if new_strategy == ExitStrategy.CLOSE_AT_FINAL_TP else "الإغلاق اليدوي فقط")
        notification_text = (f"<b>📈 تم تغيير استراتيجية الخروج لـ #{updated_rec.asset.value}</b> إلى: {strategy_text}.")
        self._notify_all_channels(rec_id, notification_text)
        return updated_rec

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
        if updated: return self.repo.update(rec)
        return None

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        return self.repo.get_recent_assets_for_user(user_id, limit)
# --- END OF FINAL, COMPLETE, AND READY-TO-USE FILE ---