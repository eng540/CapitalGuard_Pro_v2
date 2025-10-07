# src/capitalguard/application/services/trade_service.py (v21.0 - FINAL & COMPLETE)
"""
TradeService — الإصدار النهائي الكامل والداعم لتعدد المستخدمين مع ميزة التتبع الذكي.
This version contains all analyst and trader functionalities, with the UoW decorator
removed from service methods to fix the TypeError.
"""

import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    PublishedMessage, Recommendation, RecommendationEvent, User, UserType,
    RecommendationStatusEnum, UserTrade, UserTradeStatus
)
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, ChannelRepository, UserRepository
)
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType,
    ExitStrategy
)
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets
from capitalguard.interfaces.telegram.ui_texts import _pct, _calculate_weighted_pnl

log = logging.getLogger(__name__)

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try:
        return int(user_id) if user_id is not None and str(user_id).strip().isdigit() else None
    except (TypeError, ValueError):
        return None

class TradeService:
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: Any,
        price_service: Any,
        alert_service: Any,
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

    async def notify_card_update(self, rec_entity: RecommendationEntity):
        to_delete = []
        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec_entity.id)
            if not published_messages: return
            log.info("Asynchronously updating %d cards for rec #%s...", len(published_messages), rec_entity.id)
            for msg_meta in published_messages:
                try:
                    edit_fn = getattr(self.notifier, "edit_recommendation_card_by_ids", None)
                    if edit_fn is None:
                        log.error("Notifier missing 'edit_recommendation_card_by_ids' method.")
                        continue
                    await self._call_notifier_maybe_async(edit_fn, channel_id=msg_meta.telegram_channel_id, message_id=msg_meta.telegram_message_id, rec=rec_entity)
                except Exception as e:
                    err_text = str(e).lower()
                    if "message to edit not found" in err_text or "message not found" in err_text:
                        log.warning("Message %s for rec %s not found. Scheduling removal.", msg_meta.telegram_message_id, rec_entity.id)
                        to_delete.append(msg_meta)
                    else:
                        log.error("Failed to update card for rec %s on channel %s: %s", rec_entity.id, msg_meta.telegram_channel_id, e, exc_info=True)
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
        if entry <= 0 or stop_loss <= 0:
            raise ValueError("Entry and Stop Loss prices must be positive.")
        if not targets or not all(t.get('price', 0) > 0 for t in targets):
            raise ValueError("At least one valid target with a positive price is required.")
        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("For new LONG trades, Stop Loss must be < Entry Price.")
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("For new SHORT trades, Stop Loss must be > Entry Price.")
        for target in targets:
            if side_upper == 'LONG' and target['price'] <= entry:
                raise ValueError(f"Target price {target['price']} must be above entry for a LONG trade.")
            if side_upper == 'SHORT' and target['price'] >= entry:
                raise ValueError(f"Target price {target['price']} must be below entry for a SHORT trade.")
        risk = abs(entry - stop_loss)
        if risk <= 1e-9:
            raise ValueError("Entry and Stop Loss prices cannot be the same.")
        if side_upper == "LONG":
            first_target_price = min(t['price'] for t in targets)
        else:
            first_target_price = max(t['price'] for t in targets)
        reward = abs(first_target_price - entry)
        min_acceptable_rr = 0.1
        if (reward / risk) < min_acceptable_rr:
            raise ValueError(f"Risk/Reward ratio too low: {(reward / risk):.3f}. Minimum allowed: {min_acceptable_rr}")
        target_prices = [t['price'] for t in targets]
        if len(target_prices) != len(set(target_prices)):
            raise ValueError("Target prices must be unique.")
        is_long = side_upper == 'LONG'
        sorted_prices = sorted(target_prices, reverse=not is_long)
        if target_prices != sorted_prices:
            raise ValueError("Targets must be in ascending order for LONG trades and descending for SHORT trades.")
        total_close = sum(float(t.get('close_percent', 0)) for t in targets)
        if total_close > 100.01:
            raise ValueError("Sum of target close percentages cannot exceed 100%.")

    def _convert_enum_to_string(self, value):
        if hasattr(value, 'value'):
            return value.value
        return value

    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(session).find_by_telegram_id(uid_int)
        if not user:
            report["failed"].append({"reason": "User not found"})
            return rec_entity, report
        channels_to_publish = ChannelRepository(session).list_by_analyst(user.id, only_active=True)
        if target_channel_ids is not None:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
        if not channels_to_publish:
            reason = "No active channels linked." if target_channel_ids is None else "No selected channels are active or linked."
            report["failed"].append({"reason": reason})
            return rec_entity, report
        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        for ch in channels_to_publish:
            success = False
            last_exc = None
            for attempt in range(3):
                try:
                    post_fn = getattr(self.notifier, "post_to_channel", None)
                    if post_fn is None: raise RuntimeError("Notifier missing 'post_to_channel' method.")
                    res = await self._call_notifier_maybe_async(post_fn, ch.telegram_channel_id, rec_entity, keyboard)
                    if isinstance(res, tuple) and len(res) == 2:
                        publication = PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=res[0], telegram_message_id=res[1])
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
        return rec_entity, report

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(db_session).find_by_telegram_id(uid_int)
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can create recommendations.")

        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        
        order_type_input = kwargs['order_type']
        order_type_str = self._convert_enum_to_string(order_type_input)
        order_type_enum = OrderType(order_type_str)
        
        status, final_entry = (RecommendationStatusEnum.PENDING, kwargs['entry'])
        if order_type_enum == OrderType.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            if live_price is None: 
                raise RuntimeError(f"Could not fetch live price for {asset}.")
            status, final_entry = RecommendationStatusEnum.ACTIVE, live_price
        
        targets_list = kwargs['targets']
        self._validate_recommendation_data(side, final_entry, kwargs['stop_loss'], targets_list)

        rec_orm = Recommendation(
            analyst_id=user.id,
            asset=asset,
            side=side,
            entry=final_entry,
            stop_loss=kwargs['stop_loss'],
            targets=targets_list,
            order_type=order_type_str,
            status=self._convert_enum_to_string(status),
            market=market,
            notes=kwargs.get('notes'),
            exit_strategy=self._convert_enum_to_string(kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP)),
            open_size_percent=100.0,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )
        
        db_session.add(rec_orm)
        db_session.flush()

        event_type = "CREATED_ACTIVE" if rec_orm.status == RecommendationStatusEnum.ACTIVE.value else "CREATED_PENDING"
        new_event = RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={})
        db_session.add(new_event)
        db_session.flush()
        
        db_session.refresh(rec_orm)

        created_rec_entity = self.repo._to_entity(rec_orm)
        await self.alert_service.update_triggers_for_recommendation(created_rec_entity.id)
        
        final_rec, report = await self._publish_recommendation(db_session, created_rec_entity, user_id, kwargs.get('target_channel_ids'))
        return final_rec, report

    def get_recommendation_for_user(self, db_session: Session, rec_id: int, user_telegram_id: str) -> Optional[RecommendationEntity]:
        user = UserRepository(db_session).find_by_telegram_id(int(user_telegram_id))
        if not user: 
            return None
        
        if user.user_type == UserType.ANALYST:
            rec_orm = self.repo.get(db_session, rec_id)
            if not rec_orm or rec_orm.analyst_id != user.id: 
                return None
            return self.repo._to_entity(rec_orm)
        
        return None

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str, **filters) -> List[Any]:
        user = UserRepository(db_session).find_by_telegram_id(int(user_telegram_id))
        if not user: 
            return []
        
        if user.user_type == UserType.ANALYST:
            recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id)
            return [self.repo._to_entity(r) for r in recs_orm]
        else:
            trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id)
            return trades_orm

    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        user = UserRepository(db_session).find_by_telegram_id(int(user_telegram_id))
        if not user:
            return []
            
        if user.user_type == UserType.ANALYST:
            recs = self.repo.get_open_recs_for_analyst(db_session, user.id)
            assets = list(set([r.asset for r in recs]))[:limit]
        else:
            trades = self.repo.get_open_trades_for_trader(db_session, user.id)
            assets = list(set([t.asset for t in trades]))[:limit]
            
        return assets if assets else ["BTCUSDT", "ETHUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT"]

    async def cancel_pending_recommendation_manual(self, rec_id: int, user_telegram_id: str, db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(int(user_telegram_id))
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can cancel recommendations.")
            
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or rec_orm.analyst_id != user.id:
            raise ValueError("Recommendation not found or access denied.")
            
        if rec_orm.status != RecommendationStatusEnum.PENDING:
            raise ValueError("Only pending recommendations can be cancelled.")
            
        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="CANCELLED_MANUALLY",
            event_data={"cancelled_by": user_telegram_id}
        )
        db_session.add(event)
        
        await self.alert_service.remove_triggers_for_item(rec_id, is_user_trade=False)
        
        log.info(f"✅ Cancelled pending recommendation #{rec_id} by user {user_telegram_id}")
        return self.repo._to_entity(rec_orm)

    async def close_recommendation_for_user_async(self, rec_id: int, user_telegram_id: str, exit_price: float, reason: str = "MANUAL_CLOSE", db_session: Session = None) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(int(user_telegram_id))
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can close recommendations.")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.analyst_id != user.id:
            raise ValueError("Recommendation not found or access denied.")
            
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            raise ValueError("Recommendation is already closed.")
            
        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.exit_price = exit_price
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="CLOSED_MANUALLY",
            event_data={
                "exit_price": exit_price,
                "reason": reason,
                "closed_by": user_telegram_id
            }
        )
        db_session.add(event)
        
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        
        await self.alert_service.remove_triggers_for_item(rec_id, is_user_trade=False)
        
        log.info(f"✅ Closed recommendation #{rec_id} at price {exit_price} by user {user_telegram_id}")
        return rec_entity

    async def close_recommendation_at_market_for_user_async(self, rec_id: int, user_telegram_id: str, db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(int(user_telegram_id))
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can close recommendations.")
            
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or rec_orm.analyst_id != user.id:
            raise ValueError("Recommendation not found or access denied.")
            
        market_price = await self.price_service.get_cached_price(rec_orm.asset, rec_orm.market, force_refresh=True)
        if not market_price:
            raise ValueError("Could not fetch current market price.")
            
        return await self.close_recommendation_for_user_async(rec_id, user_telegram_id, market_price, "MARKET_CLOSE", db_session=db_session)

    async def update_sl_for_user_async(self, rec_id: int, user_telegram_id: str, new_sl: float, db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(int(user_telegram_id))
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can update recommendations.")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.analyst_id != user.id:
            raise ValueError("Recommendation not found or access denied.")
            
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Only active recommendations can be updated.")
            
        old_sl = rec_orm.stop_loss
        rec_orm.stop_loss = new_sl
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="SL_UPDATED",
            event_data={
                "old_sl": float(old_sl),
                "new_sl": new_sl,
                "updated_by": user_telegram_id
            }
        )
        db_session.add(event)
        
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        
        log.info(f"✅ Updated SL for recommendation #{rec_id} from {old_sl} to {new_sl} by user {user_telegram_id}")
        return rec_entity

    async def update_targets_for_user_async(self, rec_id: int, user_telegram_id: str, new_targets: List[Dict[str, float]], db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(int(user_telegram_id))
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can update recommendations.")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.analyst_id != user.id:
            raise ValueError("Recommendation not found or access denied.")
            
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Only active recommendations can be updated.")
            
        self._validate_recommendation_data(rec_orm.side, float(rec_orm.entry), float(rec_orm.stop_loss), new_targets)
        
        old_targets = rec_orm.targets
        rec_orm.targets = new_targets
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="TARGETS_UPDATED",
            event_data={
                "old_targets": old_targets,
                "new_targets": new_targets,
                "updated_by": user_telegram_id
            }
        )
        db_session.add(event)
        
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        
        log.info(f"✅ Updated targets for recommendation #{rec_id} by user {user_telegram_id}")
        return rec_entity

    async def process_activation_event(self, rec_id: int):
        with session_scope() as session:
            rec_orm = self.repo.get_for_update(session, rec_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.PENDING:
                return
                
            rec_orm.status = RecommendationStatusEnum.ACTIVE
            rec_orm.activated_at = datetime.now(timezone.utc)
            rec_orm.updated_at = datetime.now(timezone.utc)
            
            event = RecommendationEvent(
                recommendation_id=rec_id,
                event_type="ACTIVATED_AUTO",
                event_data={"activated_at": rec_orm.activated_at.isoformat()}
            )
            session.add(event)
            
            rec_entity = self.repo._to_entity(rec_orm)
            await self.notify_card_update(rec_entity)
            
            log.info(f"✅ Auto-activated recommendation #{rec_id}")

    async def process_invalidation_event(self, rec_id: int):
        with session_scope() as session:
            rec_orm = self.repo.get_for_update(session, rec_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.PENDING:
                return
                
            rec_orm.status = RecommendationStatusEnum.CLOSED
            rec_orm.closed_at = datetime.now(timezone.utc)
            rec_orm.updated_at = datetime.now(timezone.utc)
            
            event = RecommendationEvent(
                recommendation_id=rec_id,
                event_type="INVALIDATED_AUTO",
                event_data={"invalidated_at": rec_orm.closed_at.isoformat()}
            )
            session.add(event)
            
            rec_entity = self.repo._to_entity(rec_orm)
            await self.notify_card_update(rec_entity)
            
            log.info(f"🔄 Auto-invalidated recommendation #{rec_id} (SL hit before entry)")

    async def track_forwarded_trade(self, user_id: str, trade_data: Dict[str, Any], db_session: Session) -> Dict[str, Any]:
        try:
            user_repo = UserRepository(db_session)
            user = user_repo.find_by_telegram_id(int(user_id))
            
            if not user:
                return {'success': False, 'error': 'User not found'}
                
            new_trade = UserTrade(
                user_id=user.id,
                asset=trade_data['asset'],
                side=trade_data['side'],
                entry=float(trade_data['entry']),
                stop_loss=float(trade_data['stop_loss']),
                targets=trade_data['targets'],
                status=UserTradeStatus.OPEN,
                source_forwarded_text=str(trade_data)
            )
            
            db_session.add(new_trade)
            db_session.flush()
            
            await self.alert_service.build_triggers_index()
            
            log.info(f"✅ Added forwarded trade #{new_trade.id} for user {user_id} - {trade_data['asset']} {trade_data['side']}")
            
            return {
                'success': True,
                'trade_id': new_trade.id,
                'asset': new_trade.asset,
                'side': new_trade.side,
                'status': 'ADDED'
            }
            
        except Exception as e:
            log.error(f"❌ Failed to track forwarded trade for user {user_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }