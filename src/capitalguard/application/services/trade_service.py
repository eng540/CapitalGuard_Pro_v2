# src/capitalguard/application/services/trade_service.py v18.1.1 (Comprehensive Notifications)
"""
TradeService — Final version with comprehensive and consistent notification logic
for every state-changing event, ensuring the system feels alive and responsive.
"""

import logging
import asyncio
import inspect
from functools import wraps
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import PublishedMessage, RecommendationORM
from capitalguard.infrastructure.db.repository import RecommendationRepository, ChannelRepository, UserRepository
from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import NotifierPort
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

log = logging.getLogger(__name__)


def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try:
        return int(user_id) if user_id is not None and str(user_id).strip().isdigit() else None
    except (TypeError, ValueError):
        return None


def uow_transaction(func):
    is_coro = asyncio.iscoroutinefunction(func)
    if is_coro:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if 'db_session' in kwargs and isinstance(kwargs['db_session'], Session):
                return await func(*args, **kwargs)
            with session_scope() as session:
                try:
                    return await func(*args, db_session=session, **kwargs)
                except Exception:
                    log.exception("Transaction failed in async '%s'", func.__name__)
                    raise
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if 'db_session' in kwargs and isinstance(kwargs['db_session'], Session):
                return func(*args, **kwargs)
            with session_scope() as session:
                try:
                    return func(*args, db_session=session, **kwargs)
                except Exception:
                    log.exception("Transaction failed in sync '%s'", func.__name__)
                    raise
        return sync_wrapper


class TradeService:
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: NotifierPort,
        market_data_service: MarketDataService,
        price_service: PriceService,
        alert_service,
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        self.alert_service = alert_service

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def notify_card_update(self, rec: Recommendation):
        to_delete = []
        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec.id)
            if not published_messages: return
            log.info("Asynchronously updating %d cards for rec #%s...", len(published_messages), rec.id)
            for msg_meta in published_messages:
                try:
                    edit_fn = getattr(self.notifier, "edit_recommendation_card_by_ids", None)
                    if edit_fn is None:
                        log.error("Notifier missing 'edit_recommendation_card_by_ids' method.")
                        continue
                    await self._call_notifier_maybe_async(edit_fn, channel_id=msg_meta.telegram_channel_id, message_id=msg_meta.telegram_message_id, rec=rec)
                except Exception as e:
                    err_text = str(e).lower()
                    if "message to edit not found" in err_text or "message not found" in err_text:
                        log.warning("Message %s for rec %s not found. Scheduling removal.", msg_meta.telegram_message_id, rec.id)
                        to_delete.append(msg_meta)
                    else:
                        log.error("Failed to update card for rec %s on channel %s: %s", rec.id, msg_meta.telegram_channel_id, e, exc_info=True)
            for dm in to_delete:
                try:
                    session.delete(dm)
                except Exception:
                    log.exception("Failed to delete PublishedMessage %s", getattr(dm, "id", "<unknown>"))

    def notify_reply(self, rec_id: int, text: str):
        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec_id)
            for msg_meta in published_messages:
                try:
                    post_fn = getattr(self.notifier, "post_notification_reply", None)
                    if post_fn is None:
                        log.error("Notifier missing 'post_notification_reply' method.")
                        continue
                    if inspect.iscoroutinefunction(post_fn):
                        try:
                            loop = asyncio.get_running_loop()
                            asyncio.run_coroutine_threadsafe(post_fn(chat_id=msg_meta.telegram_channel_id, message_id=msg_meta.telegram_message_id, text=text), loop)
                        except RuntimeError:
                            asyncio.run(post_fn(chat_id=msg_meta.telegram_channel_id, message_id=msg_meta.telegram_message_id, text=text))
                    else:
                        post_fn(chat_id=msg_meta.telegram_channel_id, message_id=msg_meta.telegram_message_id, text=text)
                except Exception as e:
                    log.warning("Failed to send reply notification for rec #%s to channel %s: %s", rec_id, msg_meta.telegram_channel_id, e)

    def _validate_recommendation_data(self, side: str, entry: float, stop_loss: float, targets: List[Dict[str, float]]):
        side_upper = side.upper()
        if entry > 0:
            if side_upper == "LONG" and not (stop_loss < entry):
                raise ValueError("For new LONG trades, Stop Loss must be < Entry Price.")
            if side_upper == "SHORT" and not (stop_loss > entry):
                raise ValueError("For new SHORT trades, Stop Loss must be > Entry Price.")
        target_prices = [t['price'] for t in targets]
        if len(target_prices) != len(set(target_prices)):
            raise ValueError("Target prices must be unique.")
        risk = abs(entry - stop_loss)
        if not targets: raise ValueError("At least one target is required.")
        if side_upper == "LONG":
            first_targets = [t for t in targets if t['price'] > entry]
            if not first_targets: raise ValueError("At least one target must be above entry for LONG.")
            first_target = min(first_targets, key=lambda t: t['price'])
        else:
            first_targets = [t for t in targets if t['price'] < entry]
            if not first_targets: raise ValueError("At least one target must be below entry for SHORT.")
            first_target = max(first_targets, key=lambda t: t['price'])
        reward = abs(first_target['price'] - entry)
        min_acceptable_rr = 0.1
        if risk > 0 and (reward / risk) < min_acceptable_rr:
            raise ValueError(f"Risk/Reward ratio too low: {(reward / risk):.3f}. Minimum allowed: {min_acceptable_rr}")
        total_close = sum(float(t.get('close_percent', 0)) for t in targets)
        if total_close > 100.01:  # Allow for small float inaccuracies
            raise ValueError("Sum of close percentages exceeds 100%")
        is_long = side_upper == 'LONG'
        sorted_targets = sorted(targets, key=lambda t: t['price'], reverse=not is_long)
        if [t['price'] for t in targets] != [t['price'] for t in sorted_targets]:
            raise ValueError("Targets must be in ascending/descending order based on side.")
        targets_vo = Targets(targets)
        for target in targets_vo.values:
            if entry > 0:
                if (side_upper == 'LONG' and target.price <= entry) or (side_upper == 'SHORT' and target.price >= entry):
                    raise ValueError(f"Target price {target.price} is not valid for a {side} trade with entry {entry}.")
            if (side_upper == 'LONG' and target.price <= stop_loss) or (side_upper == 'SHORT' and target.price >= stop_loss):
                raise ValueError(f"Target price {target.price} cannot be on the same side of the trade as the stop loss {stop_loss}.")

    async def _publish_recommendation(self, session: Session, rec: Recommendation, user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[Recommendation, Dict]:
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(session).find_by_telegram_id(uid_int)
        if not user:
            report["failed"].append({"reason": "User not found"})
            return rec, report
        channels_to_publish = ChannelRepository(session).list_by_user(user.id, only_active=True)
        if target_channel_ids is not None:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
        if not channels_to_publish:
            reason = "No active channels linked." if target_channel_ids is None else "No selected channels are active or linked."
            report["failed"].append({"reason": reason})
            return rec, report
        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec.id, getattr(self.notifier, "bot_username", None))
        for ch in channels_to_publish:
            success = False
            last_exc = None
            for attempt in range(3):
                try:
                    post_fn = getattr(self.notifier, "post_to_channel", None)
                    if post_fn is None: raise RuntimeError("Notifier missing 'post_to_channel' method.")
                    res = await self._call_notifier_maybe_async(post_fn, ch.telegram_channel_id, rec, keyboard)
                    if isinstance(res, tuple) and len(res) == 2:
                        publication = PublishedMessage(recommendation_id=rec.id, telegram_channel_id=res[0], telegram_message_id=res[1])
                        session.add(publication)
                        report["success"].append({"channel_id": ch.telegram_channel_id, "message_id": res[1]})
                        success = True
                        break
                    else:
                        raise RuntimeError(f"Notifier returned unsupported response type: {type(res)}")
                except Exception as e:
                    last_exc = e
                    log.warning("Publish attempt %d failed for channel %s: %s", attempt + 1, ch.telegram_channel_id, e)
                    await asyncio.sleep(0.2 * (attempt + 1))
            if not success:
                err_msg = str(last_exc) if last_exc is not None else "Unknown error"
                report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": err_msg})
        try:
            session.flush()
        except Exception:
            log.exception("Failed to flush PublishedMessage records.")
        return rec, report

    @uow_transaction
    async def process_invalidation_event(self, rec_id: int, *, db_session: Session):
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.status != RecommendationStatus.PENDING:
            log.warning("Skipping invalidation for Rec #%s: Not found or status is not PENDING.", rec_id)
            await self.alert_service.remove_triggers_for_recommendation(rec_id)
            return
        rec = self.repo._to_entity(rec_orm)
        rec.status = RecommendationStatus.CLOSED
        rec.closed_at = datetime.now(timezone.utc)
        updated_rec = self.repo.update_with_event(db_session, rec, "INVALIDATED_SL_BREACH", {"reason": "Stop Loss was hit before entry price."})
        if updated_rec:
            self.notify_reply(rec_id, f"❌ <b>تم إلغاء التوصية #{updated_rec.asset.value}</b>\nتم الوصول لوقف الخسارة قبل سعر الدخول.")
            await self.notify_card_update(updated_rec)
            await self.alert_service.remove_triggers_for_recommendation(rec_id)

    @uow_transaction
    async def process_activation_event(self, rec_id: int, *, db_session: Session):
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.status != RecommendationStatus.PENDING:
            log.warning("Skipping activation for Rec #%s: Not found or status is not PENDING (current: %s).", rec_id, getattr(rec_orm, "status", "N/A"))
            await self.alert_service.update_triggers_for_recommendation(rec_id)
            return
        updated_rec = await self.activate_recommendation_async(rec_id, db_session=db_session)
        if updated_rec:
            self.notify_reply(rec_id, f"▶️ <b>تم تفعيل الصفقة</b> | تم الوصول لسعر الدخول <b>{updated_rec.asset.value}</b>.")
            await self.notify_card_update(updated_rec)
            await self.alert_service.update_triggers_for_recommendation(rec_id)

    @uow_transaction
    async def process_tp_hit_event(self, rec_id: int, user_id: str, target_index: int, price: float, *, db_session: Session):
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.status != RecommendationStatus.ACTIVE:
            log.warning("Skipping TP hit for Rec #%s: Status is not ACTIVE.", rec_id)
            return
        rec = self.repo._to_entity(rec_orm)
        event_name = f"TP{target_index}_HIT"
        if event_name in {e.event_type for e in rec.events}:
            log.warning("Skipping %s for Rec #%s: Event already processed.", event_name, rec_id)
            return
        updated_rec = await self.process_target_hit_async(rec_id, user_id, target_index, price, db_session=db_session)
        if updated_rec:
            target = updated_rec.targets.values[target_index - 1]
            self.notify_reply(rec_id, f"🔥 <b>تم تحقيق الهدف {target_index}!</b> | <b>{updated_rec.asset.value}</b> وصل إلى <b>{target.price:g}</b>.")
            if target.close_percent > 0:
                pnl_on_part = _pct(updated_rec.entry.value, price, updated_rec.side.value)
                notification_text = (f"💰 <b>جني ربح جزئي</b> | توصية #{rec_id}\n\n"
                                   f"تم إغلاق <b>{target.close_percent:.2f}%</b> من <b>{updated_rec.asset.value}</b> عند سعر <b>{price:g}</b> بربح <b>{pnl_on_part:+.2f}%</b>.\n\n"
                                   f"<i>الحجم المتبقي المفتوح: {updated_rec.open_size_percent:.2f}%</i>")
                self.notify_reply(rec_id, notification_text)
            await self.notify_card_update(updated_rec)
            if updated_rec.status == RecommendationStatus.CLOSED:
                await self.alert_service.remove_triggers_for_recommendation(rec_id)
            else:
                await self.alert_service.update_triggers_for_recommendation(rec_id)

    @uow_transaction
    async def process_sl_hit_event(self, rec_id: int, user_id: str, price: float, *, db_session: Session):
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.status != RecommendationStatus.ACTIVE:
            log.warning("Skipping SL hit for Rec #%s: Not found or not ACTIVE.", rec_id)
            return
        await self.close_recommendation_for_user_async(rec_id, user_id, price, reason="SL_HIT", db_session=db_session)

    @uow_transaction
    async def process_profit_stop_hit_event(self, rec_id: int, user_id: str, price: float, *, db_session: Session):
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.status != RecommendationStatus.ACTIVE:
            log.warning("Skipping Profit Stop hit for Rec #%s: Not found or not ACTIVE.", rec_id)
            return
        await self.close_recommendation_for_user_async(rec_id, user_id, price, reason="PROFIT_STOP_HIT", db_session=db_session)

    @uow_transaction
    async def create_and_publish_recommendation_async(self, *, db_session: Session, **kwargs) -> Tuple[Recommendation, Dict]:
        uid_int = _parse_int_user_id(kwargs.get('user_id'))
        if not uid_int: raise ValueError("A valid user_id is required.")
        target_channel_ids = kwargs.get('target_channel_ids')
        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        if not self.market_data_service.is_valid_symbol(asset, market):
            raise ValueError(f"The symbol '{asset}' is not valid or available in the '{market}' market.")
        order_type_enum = OrderType(kwargs['order_type'].upper())
        status, final_entry = (RecommendationStatus.PENDING, kwargs['entry'])
        if order_type_enum == OrderType.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            if live_price is None: raise RuntimeError(f"Could not fetch live price for {asset}.")
            status, final_entry = RecommendationStatus.ACTIVE, live_price
        targets_list = kwargs['targets']
        is_long = side == "LONG"
        targets_list.sort(key=lambda t: t['price'], reverse=not is_long)
        self._validate_recommendation_data(side, final_entry, kwargs['stop_loss'], targets_list)
        rec_entity = Recommendation(
            asset=Symbol(asset), side=Side(side), entry=Price(final_entry),
            stop_loss=Price(kwargs['stop_loss']), targets=Targets(targets_list),
            order_type=order_type_enum, status=status, market=market, notes=kwargs.get('notes'),
            user_id=str(uid_int), exit_strategy=kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP),
            open_size_percent=100.0,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None
        )
        if rec_entity.status == RecommendationStatus.ACTIVE:
            rec_entity.highest_price_reached = rec_entity.lowest_price_reached = rec_entity.entry.value
        created_rec = self.repo.add_with_event(db_session, rec_entity)
        await self.alert_service.update_triggers_for_recommendation(created_rec.id)
        final_rec, report = await self._publish_recommendation(db_session, created_rec, str(uid_int), target_channel_ids)
        return final_rec, report

    @uow_transaction
    async def cancel_pending_recommendation_manual(self, rec_id: int, user_telegram_id: str, *, db_session: Session) -> Recommendation:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != str(uid_int): raise ValueError(f"Recommendation #{rec_id} not found or access denied.")
        if rec.status != RecommendationStatus.PENDING: raise ValueError("Only PENDING recommendations can be cancelled.")
        rec.status = RecommendationStatus.CLOSED
        rec.closed_at = datetime.now(timezone.utc)
        updated_rec = self.repo.update_with_event(db_session, rec, "CANCELED_MANUAL", {"reason": "Cancelled manually by the user."})
        
        if updated_rec:
            self.notify_reply(rec_id, f"❌ <b>تم إلغاء التوصية #{updated_rec.asset.value}</b> يدويًا.")
            await self.notify_card_update(updated_rec)

        await self.alert_service.remove_triggers_for_recommendation(rec_id)
        return updated_rec

    @uow_transaction
    async def close_recommendation_for_user_async(self, rec_id: int, user_telegram_id: str, exit_price: float, reason: str = "MANUAL_CLOSE", *, db_session: Session) -> Recommendation:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != str(uid_int): raise ValueError(f"Recommendation #{rec_id} not found or access denied.")
        if rec.status == RecommendationStatus.CLOSED: return rec
        
        rec.open_size_percent = 0.0
        rec.close(exit_price)
        updated_rec = self.repo.update_with_event(db_session, rec, "CLOSED", {"exit_price": exit_price, "reason": reason})
        
        if updated_rec:
            pnl = _pct(updated_rec.entry.value, exit_price, updated_rec.side.value)
            emoji, r_text = ("🏆", "ربح") if pnl > 0.001 else ("💔", "خسارة")
            self.notify_reply(rec_id, f"<b>{emoji} تم إغلاق الصفقة #{updated_rec.asset.value}</b>\nأُغلقت عند {exit_price:g} بنتيجة <b>{pnl:+.2f}%</b> ({r_text}).")
            await self.notify_card_update(updated_rec)

        await self.alert_service.remove_triggers_for_recommendation(rec_id)
        return updated_rec

    async def close_recommendation_at_market_for_user_async(self, rec_id: int, user_telegram_id: str) -> Recommendation:
        with session_scope() as session:
            rec = self.repo.get_by_id_for_user(session, rec_id, user_telegram_id)
        if not rec: raise ValueError(f"Recommendation #{rec_id} not found or access denied.")
        live_price = await self.price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
        if live_price is None: raise RuntimeError(f"Could not fetch live market price for {rec.asset.value}.")
        return await self.close_recommendation_for_user_async(rec_id, user_telegram_id, live_price, reason="MANUAL_MARKET_CLOSE")

    @uow_transaction
    async def activate_recommendation_async(self, rec_id: int, *, db_session: Session) -> Optional[Recommendation]:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: return None
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.status != RecommendationStatus.PENDING: return rec
        rec.activate()
        rec.highest_price_reached = rec.lowest_price_reached = rec.entry.value
        updated_rec = self.repo.update_with_event(db_session, rec, "ACTIVATED", {})
        return updated_rec

    async def _take_partial_profit_atomic(self, rec_orm: RecommendationORM, user_id: str, close_percent: float, price: float, triggered_by: str, *, db_session: Session) -> Recommendation:
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access denied.")
        if rec.status != RecommendationStatus.ACTIVE: raise ValueError("Partial profit can only be taken on active recommendations.")
        if not (0 < close_percent <= rec.open_size_percent + 0.1):  # Add tolerance
            raise ValueError(f"Invalid percentage. Must be between 0 and {rec.open_size_percent:.2f}.")
        rec.open_size_percent -= close_percent
        pnl_on_part = _pct(rec.entry.value, price, rec.side.value)
        event_type = "PARTIAL_PROFIT_AUTO" if triggered_by.upper() == "AUTO" else "PARTIAL_PROFIT_MANUAL"
        event_data = {"price": price, "closed_percent": close_percent, "remaining_percent": rec.open_size_percent, "pnl_on_part": pnl_on_part, "triggered_by": triggered_by}
        updated_rec = self.repo.update_with_event(db_session, rec, event_type, event_data)
        if updated_rec.open_size_percent <= 0.01:
            log.info("Recommendation #%s fully closed via partial profits. Marking as closed.", rec.id)
            reason = "AUTO_PARTIAL_FULL_CLOSE" if triggered_by.upper() == "AUTO" else "MANUAL_PARTIAL_FULL_CLOSE"
            return await self.close_recommendation_for_user_async(rec.id, user_id, price, reason=reason, db_session=db_session)
        return updated_rec

    @uow_transaction
    async def process_target_hit_async(self, rec_id: int, user_id: str, target_index: int, hit_price: float, *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec_orm or rec.status != RecommendationStatus.ACTIVE: return rec
        if not rec.targets.values or len(rec.targets.values) < target_index: return rec
        target = rec.targets.values[target_index - 1]
        event_type = f"TP{target_index}_HIT"
        updated_rec = self.repo.update_with_event(db_session, rec, event_type, {"price": hit_price, "target": target.price})
        if target.close_percent > 0:
            log.info("Auto partial profit triggered for rec #%s at TP%s.", rec_id, target_index)
            updated_rec = await self._take_partial_profit_atomic(rec_orm, user_id, target.close_percent, target.price, triggered_by="AUTO", db_session=db_session)
        return updated_rec

    @uow_transaction
    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: float, *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access Denied.")
        if rec.status == RecommendationStatus.CLOSED: raise ValueError("Cannot update SL for a closed recommendation.")
        old_sl = rec.stop_loss.value
        rec.stop_loss = Price(new_sl)
        updated_rec = self.repo.update_with_event(db_session, rec, "SL_UPDATED", {"old_sl": old_sl, "new_sl": new_sl})
        await self.notify_card_update(updated_rec)
        self.notify_reply(rec_id, f"✏️ <b>تم تحديث وقف الخسارة</b> لـ #{rec.asset.value} إلى <b>{new_sl:g}</b>.")
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        return updated_rec

    @uow_transaction
    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, float]], *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access Denied.")
        if rec.status == RecommendationStatus.CLOSED: raise ValueError("Cannot update targets for a closed recommendation.")
        old_targets = [t.price for t in rec.targets.values]
        rec.targets = Targets(new_targets)
        updated_rec = self.repo.update_with_event(db_session, rec, "TARGETS_UPDATED", {"old": old_targets, "new": [t.price for t in rec.targets.values]})
        await self.notify_card_update(updated_rec)
        self.notify_reply(rec_id, f"🎯 <b>تم تحديث الأهداف</b> لـ #{rec.asset.value}.")
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        return updated_rec

    @uow_transaction
    async def update_exit_strategy_for_user_async(self, rec_id: int, user_id: str, new_strategy: ExitStrategy, *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access Denied.")
        if rec.status == RecommendationStatus.CLOSED: return rec
        old_strategy = rec.exit_strategy
        rec.exit_strategy = new_strategy
        updated_rec = self.repo.update_with_event(db_session, rec, "STRATEGY_UPDATED", {"old": old_strategy.value, "new": new_strategy.value})
        await self.notify_card_update(updated_rec)
        self.notify_reply(rec_id, f"📈 <b>تم تحديث استراتيجية الخروج</b> لـ #{rec.asset.value}.")
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        return updated_rec

    @uow_transaction
    async def update_profit_stop_for_user_async(self, rec_id: int, user_id: str, new_price: Optional[float], *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access Denied.")
        if rec.status != RecommendationStatus.ACTIVE: raise ValueError("Profit Stop can only be set on active recommendations.")
        old_price = rec.profit_stop_price
        rec.profit_stop_price = new_price
        updated_rec = self.repo.update_with_event(db_session, rec, "PROFIT_STOP_UPDATED", {"old": old_price, "new": new_price})
        await self.notify_card_update(updated_rec)
        note = f"🛡️ <b>تم تفعيل وقف الربح</b> لـ #{rec.asset.value} عند <b>{new_price:g}</b>." if new_price is not None else f"🗑️ <b>تمت إزالة وقف الربح</b> لـ #{rec.asset.value}."
        self.notify_reply(rec_id, note)
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        return updated_rec

    def get_recommendation_for_user(self, session: Session, rec_id: int, user_telegram_id: str) -> Optional[Recommendation]:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID format.")
        return self.repo.get_by_id_for_user(session, rec_id, uid_int)

    def get_open_recommendations_for_user(self, session: Session, user_telegram_id: str, **filters) -> List[Recommendation]:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: return []
        return self.repo.list_open_for_user(session, uid_int, **filters)

    def get_recent_assets_for_user(self, session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: return []
        return self.repo.get_recent_assets_for_user(session, user_telegram_id=uid_int, limit=limit)