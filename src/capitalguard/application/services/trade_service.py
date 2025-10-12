# src/capitalguard/application/services/trade_service.py (v28.4 - Final & Production Ready)
"""
TradeService - This is the definitive, feature-complete, and reliable version.
It includes full, intelligent implementations for all management features like
partial profit taking and exit strategies, with robust event logging, state
management, and idempotency checks. This file is 100% complete.
"""

import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    PublishedMessage, Recommendation, RecommendationEvent, User,
    RecommendationStatusEnum, UserTrade, UserTradeStatus, OrderTypeEnum, ExitStrategyEnum
)
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, ChannelRepository, UserRepository
)
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType,
    ExitStrategy,
    UserType
)
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets
from capitalguard.interfaces.telegram.ui_texts import _pct

if False:
    from .alert_service import AlertService
    from .price_service import PriceService
    from .market_data_service import MarketDataService

logger = logging.getLogger(__name__)

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """Safely parses a string user ID into an integer."""
    try:
        if user_id is None: return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None

class TradeService:
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: "MarketDataService",
        price_service: "PriceService",
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        self.alert_service: "AlertService" = None

    async def _commit_and_dispatch(self, db_session: Session, rec_orm: Recommendation, rebuild_alerts: bool = True):
        """
        The single source of truth for committing recommendation changes and dispatching updates.
        """
        db_session.commit()
        db_session.refresh(rec_orm, ['events', 'analyst'])
        
        if rebuild_alerts:
            await self.alert_service.build_triggers_index()
        
        updated_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(updated_entity, db_session)

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        if rec_entity.is_shadow: return
        published_messages = self.repo.get_published_messages(db_session, rec_entity.id)
        if not published_messages: return
        tasks = [
            self._call_notifier_maybe_async(
                self.notifier.edit_recommendation_card_by_ids, 
                channel_id=msg_meta.telegram_channel_id, 
                message_id=msg_meta.telegram_message_id, 
                rec=rec_entity
            ) for msg_meta in published_messages
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception): logger.error("Failed to notify card update: %s", res)

    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or rec_orm.is_shadow: return
        published_messages = self.repo.get_published_messages(db_session, rec_id)
        for msg_meta in published_messages:
            asyncio.create_task(self._call_notifier_maybe_async(
                self.notifier.post_notification_reply, 
                chat_id=msg_meta.telegram_channel_id, 
                message_id=msg_meta.telegram_message_id, 
                text=text
            ))

    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        side_upper = side.upper()
        if not all(isinstance(p, Decimal) and p > Decimal(0) for p in [entry, stop_loss]): raise ValueError("Entry and Stop Loss must be positive.")
        if not targets or not all(isinstance(t.get('price'), Decimal) and t.get('price', Decimal(0)) > Decimal(0) for t in targets): raise ValueError("At least one valid target is required.")
        if side_upper == "LONG" and stop_loss >= entry: raise ValueError("For LONG, SL must be < Entry.")
        if side_upper == "SHORT" and stop_loss <= entry: raise ValueError("For SHORT, SL must be > Entry.")
        target_prices = [t['price'] for t in targets]
        if side_upper == 'LONG' and any(p <= entry for p in target_prices): raise ValueError("All LONG targets must be > entry.")
        if side_upper == 'SHORT' and any(p >= entry for p in target_prices): raise ValueError("All SHORT targets must be < entry.")
        risk = abs(entry - stop_loss)
        if risk.is_zero(): raise ValueError("Entry and Stop Loss cannot be the same.")
        first_target_price = min(target_prices) if side_upper == "LONG" else max(target_prices)
        reward = abs(first_target_price - entry)
        if (reward / risk) < Decimal('0.1'): raise ValueError(f"Risk/Reward ratio is too low.")
        if len(target_prices) != len(set(target_prices)): raise ValueError("Target prices must be unique.")
        sorted_prices = sorted(target_prices, reverse=(side_upper == 'SHORT'))
        if target_prices != sorted_prices: raise ValueError("Targets must be in ascending order for LONGs and descending for SHORTs.")

    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        user = UserRepository(session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            report["failed"].append({"reason": "User not found"})
            return rec_entity, report
        channels_to_publish = ChannelRepository(session).list_by_analyst(user.id, only_active=True)
        if target_channel_ids: channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
        if not channels_to_publish:
            report["failed"].append({"reason": "No active channels linked."})
            return rec_entity, report
        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        for channel in channels_to_publish:
            try:
                result = await self._call_notifier_maybe_async(self.notifier.post_to_channel, channel.telegram_channel_id, rec_entity, keyboard)
                if isinstance(result, tuple) and len(result) == 2:
                    session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=result[0], telegram_message_id=result[1]))
                    report["success"].append({"channel_id": channel.telegram_channel_id, "message_id": result[1]})
                else: raise RuntimeError(f"Notifier returned unsupported type: {type(result)}")
            except Exception as e:
                report["failed"].append({"channel_id": channel.telegram_channel_id, "reason": str(e)})
        session.flush()
        return rec_entity, report

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user or user.user_type != UserType.ANALYST: raise ValueError("Only analysts can create recommendations.")
        entry_price, sl_price = kwargs['entry'], kwargs['stop_loss']
        targets_list = kwargs['targets']
        asset, side, market = kwargs['asset'].strip().upper(), kwargs['side'].upper(), kwargs.get('market', 'Futures')
        order_type_enum = OrderTypeEnum[kwargs['order_type'].upper()]
        status, final_entry = (RecommendationStatusEnum.ACTIVE, Decimal(str(await self.price_service.get_cached_price(asset, market, force_refresh=True)))) if order_type_enum == OrderTypeEnum.MARKET else (RecommendationStatusEnum.PENDING, entry_price)
        if status == RecommendationStatusEnum.ACTIVE and (final_entry is None or not final_entry.is_finite()): raise RuntimeError(f"Could not fetch live price for {asset}.")
        self._validate_recommendation_data(side, final_entry, sl_price, targets_list)
        rec_orm = Recommendation(
            analyst_id=user.id, asset=asset, side=side, entry=final_entry, stop_loss=sl_price, targets=targets_list,
            order_type=order_type_enum, status=status, market=market, notes=kwargs.get('notes'), 
            exit_strategy=ExitStrategyEnum[kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP).value],
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )
        db_session.add(rec_orm); db_session.flush()
        db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type="CREATED_ACTIVE" if status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING"))
        db_session.flush(); db_session.refresh(rec_orm)
        created_rec_entity = self.repo._to_entity(rec_orm)
        final_rec, report = await self._publish_recommendation(db_session, created_rec_entity, user_id, kwargs.get('target_channel_ids'))
        await self.alert_service.build_triggers_index()
        return final_rec, report

    async def create_trade_from_recommendation(self, user_id: str, rec_id: int, db_session: Session) -> Dict[str, Any]:
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user: return {'success': False, 'error': 'User not found'}
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm: return {'success': False, 'error': 'Recommendation not found'}
        
        existing_trade = db_session.query(UserTrade).filter(
            UserTrade.user_id == trader_user.id, 
            UserTrade.source_recommendation_id == rec_id,
            UserTrade.status == UserTradeStatus.OPEN
        ).first()

        if existing_trade: return {'success': False, 'error': 'You are already tracking this signal.'}
        
        new_trade = UserTrade(
            user_id=trader_user.id, source_recommendation_id=rec_orm.id,
            asset=rec_orm.asset, side=rec_orm.side, entry=rec_orm.entry,
            stop_loss=rec_orm.stop_loss, targets=rec_orm.targets, status=UserTradeStatus.OPEN
        )
        db_session.add(new_trade); db_session.flush()
        await self.alert_service.build_triggers_index()
        return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        
    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied: Not owner.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")
        old_sl, rec_orm.stop_loss = rec_orm.stop_loss, new_sl
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="SL_UPDATED", event_data={"old": float(old_sl), "new": float(new_sl)}))
        self.notify_reply(rec_id, f"⚠️ Stop Loss for #{rec_orm.asset} updated to {new_sl:g}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, Any]], db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")
        old_targets, rec_orm.targets = rec_orm.targets, [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in new_targets]
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="TP_UPDATED", event_data={"old": old_targets, "new": rec_orm.targets}))
        self.notify_reply(rec_id, f"🎯 Targets for #{rec_orm.asset} have been updated.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_exit_strategy_async(self, rec_id: int, user_id: str, new_strategy: ExitStrategy, db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")
        old_strategy, rec_orm.exit_strategy = rec_orm.exit_strategy, new_strategy
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="STRATEGY_UPDATED", event_data={"old": old_strategy.value, "new": new_strategy.value}))
        strategy_text = "Auto-close at final TP" if new_strategy == ExitStrategy.CLOSE_AT_FINAL_TP else "Manual close only"
        self.notify_reply(rec_id, f"📈 Exit strategy for #{rec_orm.asset} updated to: {strategy_text}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def close_recommendation_async(self, rec_id: int, user_id: str, exit_price: Decimal, db_session: Session, reason: str = "MANUAL_CLOSE") -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED: raise ValueError("Recommendation is already closed.")
        
        remaining_percent = Decimal(str(rec_orm.open_size_percent))
        if remaining_percent > 0:
            pnl_on_part = _pct(rec_orm.entry, exit_price, rec_orm.side)
            event_data = {
                "price": float(exit_price), "closed_percent": float(remaining_percent), 
                "remaining_percent": 0.0, "pnl_on_part": pnl_on_part, "triggered_by": reason
            }
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="FINAL_PARTIAL_CLOSE", event_data=event_data))

        rec_orm.status, rec_orm.exit_price, rec_orm.closed_at, rec_orm.open_size_percent = RecommendationStatusEnum.CLOSED, exit_price, datetime.now(timezone.utc), Decimal(0)
        
        self.notify_reply(rec_id, f"✅ Signal #{rec_orm.asset} manually closed at {exit_price:g}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, price: Decimal, db_session: Session, triggered_by: str = "MANUAL") -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Partial close can only be performed on active recommendations.")
        
        current_open_percent = Decimal(str(rec_orm.open_size_percent))
        actual_close_percent = min(close_percent, current_open_percent)

        if not (Decimal(0) < actual_close_percent):
            raise ValueError(f"Invalid percentage. Open position is {current_open_percent:.2f}%.")

        rec_orm.open_size_percent = current_open_percent - actual_close_percent
        pnl_on_part = _pct(rec_orm.entry, price, rec_orm.side)
        
        event_type = "PARTIAL_CLOSE_AUTO" if triggered_by == "AUTO" else "PARTIAL_CLOSE_MANUAL"
        event_data = {
            "price": float(price), 
            "closed_percent": float(actual_close_percent), 
            "remaining_percent": float(rec_orm.open_size_percent), 
            "pnl_on_part": pnl_on_part
        }
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type=event_type, event_data=event_data))
        
        if pnl_on_part >= 0:
            notif_text = f"💰 Partial Close (Profit) on #{rec_orm.asset}. Closed {actual_close_percent:g}% at {price:g} ({pnl_on_part:+.2f}%)."
        else:
            notif_text = f"⚠️ Partial Close (Loss Mgt) on #{rec_orm.asset}. Closed {actual_close_percent:g}% at {price:g} ({pnl_on_part:+.2f}%)."
        notif_text += f"\nRemaining: {rec_orm.open_size_percent:g}%"
        self.notify_reply(rec_id, notif_text, db_session)

        if rec_orm.open_size_percent < Decimal('0.1'):
            logger.info(f"Position #{rec_id} fully closed via partial close. Closing recommendation.")
            return await self.close_recommendation_async(rec_id, user_id, price, db_session, reason="PARTIAL_CLOSE_FINAL")
        else:
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)
            return self.repo._to_entity(rec_orm)

    async def process_invalidation_event(self, item_id: int):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING: return
            rec.status = RecommendationStatusEnum.CLOSED
            rec.closed_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED", event_data={"reason": "SL hit before entry"}))
            self.notify_reply(rec.id, f"❌ Signal #{rec.asset} was invalidated (SL hit before entry).", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec)

    async def process_activation_event(self, item_id: int):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING: return
            rec.status = RecommendationStatusEnum.ACTIVE
            rec.activated_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))
            self.notify_reply(rec.id, f"▶️ Signal #{rec.asset} is now ACTIVE!", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec)

    async def process_sl_hit_event(self, item_id: int, price: Decimal):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.ACTIVE: return
            await self.close_recommendation_async(rec.id, str(rec.analyst.telegram_user_id), price, db_session, reason="SL_HIT")

    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        with session_scope() as db_session:
            rec_orm = self.repo.get_for_update(db_session, item_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE: return

            event_type = f"TP{target_index}_HIT"
            if any(e.event_type == event_type for e in rec_orm.events): return

            db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={"price": float(price)}))
            self.notify_reply(rec_orm.id, f"🎯 Signal #{rec_orm.asset} hit TP{target_index} at {price}!", db_session=db_session)
            
            target_info = rec_orm.targets[target_index - 1]
            close_percent = Decimal(str(target_info.get("close_percent", 0)))

            if close_percent > 0:
                await self.partial_close_async(rec_orm.id, str(rec_orm.analyst.telegram_user_id), close_percent, price, db_session, triggered_by="AUTO")
                rec_orm = self.repo.get_for_update(db_session, item_id)

            is_final_tp = (target_index == len(rec_orm.targets))
            if (rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final_tp) or rec_orm.open_size_percent < Decimal('0.1'):
                await self.close_recommendation_async(rec_orm.id, str(rec_orm.analyst.telegram_user_id), price, db_session, reason="AUTO_CLOSE_FINAL_TP")
            else:
                await self._commit_and_dispatch(db_session, rec_orm)

    def _get_or_create_system_user(self, db_session: Session) -> User:
        system_user = db_session.query(User).filter(User.telegram_user_id == -1).first()
        if not system_user:
            system_user = User(telegram_user_id=-1, username='system', user_type=UserType.ANALYST.value, is_active=True)
            db_session.add(system_user)
            db_session.flush()
        elif not system_user.is_active:
            system_user.is_active = True
        return system_user

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: return []
        open_positions = []
        if user.user_type == UserType.ANALYST:
            recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in recs_orm:
                if rec_entity := self.repo._to_entity(rec):
                    setattr(rec_entity, 'is_user_trade', False)
                    open_positions.append(rec_entity)
        trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trades_orm:
            trade_entity = RecommendationEntity(
                id=trade.id, asset=Symbol(trade.asset), side=Side(trade.side), entry=Price(trade.entry),
                stop_loss=Price(trade.stop_loss), targets=Targets(trade.targets), status=RecommendationStatusEntity.ACTIVE,
                order_type=OrderType.MARKET, created_at=trade.created_at
            )
            setattr(trade_entity, 'is_user_trade', True)
            open_positions.append(trade_entity)
        open_positions.sort(key=lambda p: p.created_at, reverse=True)
        return open_positions

    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: return None
        if position_type == 'rec':
            if user.user_type != UserType.ANALYST: return None
            rec_orm = self.repo.get(db_session, position_id)
            if not rec_orm or rec_orm.analyst_id != user.id: return None
            if rec_entity := self.repo._to_entity(rec_orm): 
                setattr(rec_entity, 'is_user_trade', False)
            return rec_entity
        elif position_type == 'trade':
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if not trade_orm or trade_orm.user_id != user.id: return None
            trade_entity = RecommendationEntity(
                id=trade_orm.id, asset=Symbol(trade_orm.asset), side=Side(trade_orm.side), entry=Price(trade_orm.entry),
                stop_loss=Price(trade_orm.stop_loss), targets=Targets(trade_orm.targets),
                status=RecommendationStatusEntity.ACTIVE if trade_orm.status == UserTradeStatus.OPEN else RecommendationStatusEntity.CLOSED,
                order_type=OrderType.MARKET, created_at=trade_orm.created_at, closed_at=trade_orm.closed_at,
                exit_price=float(trade_orm.close_price) if trade_orm.close_price else None
            )
            setattr(trade_entity, 'is_user_trade', True)
            return trade_entity
        return None

    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: return []
        if user.user_type == UserType.ANALYST:
            recs = self.repo.get_open_recs_for_analyst(db_session, user.id)
            assets = list(dict.fromkeys([r.asset for r in recs]))[:limit]
        else:
            trades = self.repo.get_open_trades_for_trader(db_session, user.id)
            assets = list(dict.fromkeys([t.asset for t in trades]))[:limit]
        if len(assets) < limit:
            default_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
            for asset in default_assets:
                if asset not in assets and len(assets) < limit:
                    assets.append(asset)
        return assets