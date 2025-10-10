# src/capitalguard/application/services/trade_service.py (v26.1 - COMPLETE, FINAL, PRODUCTION-READY & FEATURE-COMPLETE)
"""
TradeService - The re-architected, event-driven, and robust core of the system.
This version includes a full suite of modification methods (SL, TP, Close),
correctly handles transaction scopes for all call origins, and is presented
as a complete, final file ready for production deployment.
"""

import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import uow_transaction, session_scope
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
    """
    The primary application service for managing the lifecycle of recommendations and user trades.
    """
    
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

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """Safely calls a function that might be sync or async."""
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        """Sends an update to all published Telegram messages for a recommendation."""
        if rec_entity.is_shadow: 
            return

        published_messages = self.repo.get_published_messages(db_session, rec_entity.id)
        if not published_messages: 
            return
        
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
            if isinstance(res, Exception):
                logger.error("Failed to notify card update: %s", res)

    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        """Posts a reply to all published messages for a recommendation."""
        rec = self.repo.get(db_session, rec_id)
        if not rec or rec.is_shadow: 
            return

        published_messages = self.repo.get_published_messages(db_session, rec_id)
        for msg_meta in published_messages:
            asyncio.create_task(self._call_notifier_maybe_async(
                self.notifier.post_notification_reply, 
                chat_id=msg_meta.telegram_channel_id, 
                message_id=msg_meta.telegram_message_id, 
                text=text
            ))

    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        """Fortified validation logic using Decimal for precision."""
        side_upper = side.upper()
        if not all(isinstance(p, Decimal) and p > Decimal(0) for p in [entry, stop_loss]):
            raise ValueError("Entry and Stop Loss prices must be positive numbers.")
        if not targets or not all(isinstance(t.get('price'), Decimal) and t.get('price', Decimal(0)) > Decimal(0) for t in targets):
            raise ValueError("At least one valid target with a positive price is required.")
        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("For LONG trades, Stop Loss must be < Entry Price.")
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("For SHORT trades, Stop Loss must be > Entry Price.")
        target_prices = [t['price'] for t in targets]
        if side_upper == 'LONG' and any(p <= entry for p in target_prices):
            raise ValueError("All target prices must be above entry for a LONG trade.")
        if side_upper == 'SHORT' and any(p >= entry for p in target_prices):
            raise ValueError("All target prices must be below entry for a SHORT trade.")

    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """Handles the logic of publishing a recommendation to the relevant channels."""
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

    @uow_transaction
    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user or user.user_type != UserType.ANALYST: raise ValueError("Only analysts can create recommendations.")
        entry_price, sl_price = Decimal(str(kwargs['entry'])), Decimal(str(kwargs['stop_loss']))
        targets_list = [{'price': Decimal(str(t['price'])), 'close_percent': t.get('close_percent', 0)} for t in kwargs['targets']]
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

    @uow_transaction
    async def create_trade_from_forwarding(self, user_id: str, trade_data: Dict[str, Any], db_session: Session) -> Dict[str, Any]:
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user: return {'success': False, 'error': 'User not found'}
        system_user = self._get_or_create_system_user(db_session)
        # Convert prices to string for JSON serialization, as per db engine setup
        targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0)} for t in trade_data['targets']]
        shadow_rec = Recommendation(
            analyst_id=system_user.id, asset=trade_data['asset'], side=trade_data['side'],
            entry=Decimal(str(trade_data['entry'])), stop_loss=Decimal(str(trade_data['stop_loss'])),
            targets=targets_for_db, status=RecommendationStatusEnum.ACTIVE, 
            order_type=OrderTypeEnum.MARKET, is_shadow=True,
            notes="Shadow rec from forwarded trade.", activated_at=datetime.now(timezone.utc)
        )
        db_session.add(shadow_rec); db_session.flush()
        new_trade = UserTrade(
            user_id=trader_user.id, source_recommendation_id=shadow_rec.id, asset=trade_data['asset'], side=trade_data['side'],
            entry=Decimal(str(trade_data['entry'])), stop_loss=Decimal(str(trade_data['stop_loss'])),
            targets=targets_for_db, status=UserTradeStatus.OPEN
        )
        db_session.add(new_trade); db_session.flush()
        await self.alert_service.build_triggers_index()
        return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        
    @uow_transaction
    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied: Not owner.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")

        old_sl, rec_orm.stop_loss = rec_orm.stop_loss, new_sl
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="SL_UPDATED", event_data={"old": float(old_sl), "new": float(new_sl)}))
        
        updated_entity = self.repo._to_entity(rec_orm)
        await self.alert_service.build_triggers_index()
        self.notify_reply(rec_id, f"⚠️ Stop Loss for #{updated_entity.asset.value} updated to {new_sl:g}.", db_session)
        await self.notify_card_update(updated_entity, db_session)
        return updated_entity

    @uow_transaction
    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, Any]], db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")

        old_targets, rec_orm.targets = rec_orm.targets, [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in new_targets]
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="TP_UPDATED", event_data={"old": old_targets, "new": rec_orm.targets}))
        
        updated_entity = self.repo._to_entity(rec_orm)
        await self.alert_service.build_triggers_index()
        self.notify_reply(rec_id, f"🎯 Targets for #{updated_entity.asset.value} have been updated.", db_session)
        await self.notify_card_update(updated_entity, db_session)
        return updated_entity

    @uow_transaction
    async def close_recommendation_async(self, rec_id: int, user_id: str, exit_price: Decimal, db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED: raise ValueError("Recommendation is already closed.")
        
        rec_orm.status, rec_orm.exit_price, rec_orm.closed_at = RecommendationStatusEnum.CLOSED, exit_price, datetime.now(timezone.utc)
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="MANUAL_CLOSE", event_data={"price": float(exit_price)}))

        updated_entity = self.repo._to_entity(rec_orm)
        await self.alert_service.build_triggers_index()
        self.notify_reply(rec_id, f"✅ Signal #{updated_entity.asset.value} manually closed at {exit_price:g}.", db_session)
        await self.notify_card_update(updated_entity, db_session)
        return updated_entity

    async def process_invalidation_event(self, item_id: int):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING: return
            rec.status = RecommendationStatusEnum.CLOSED
            rec.closed_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED", event_data={"reason": "SL hit before entry"}))
            db_session.commit()
            rec_entity = self.repo._to_entity(rec)
            self.notify_reply(rec.id, f"❌ Signal #{rec.asset} was invalidated (SL hit before entry).", db_session=db_session)
            await self.notify_card_update(rec_entity, db_session=db_session)
        await self.alert_service.build_triggers_index()

    async def process_activation_event(self, item_id: int):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING: return
            rec.status = RecommendationStatusEnum.ACTIVE
            rec.activated_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))
            db_session.commit()
            rec_entity = self.repo._to_entity(rec)
            self.notify_reply(rec.id, f"▶️ Signal #{rec.asset} is now ACTIVE!", db_session=db_session)
            await self.notify_card_update(rec_entity, db_session=db_session)
        await self.alert_service.build_triggers_index()

    async def process_sl_hit_event(self, item_id: int, price: Decimal):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.ACTIVE: return
            rec.status = RecommendationStatusEnum.CLOSED
            rec.closed_at = datetime.now(timezone.utc)
            rec.exit_price = price
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="SL_HIT", event_data={"price": float(price)}))
            db_session.commit()
            rec_entity = self.repo._to_entity(rec)
            self.notify_reply(rec.id, f"🛑 Signal #{rec.asset} hit Stop Loss at {price}.", db_session=db_session)
            await self.notify_card_update(rec_entity, db_session=db_session)
        await self.alert_service.build_triggers_index()

    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.ACTIVE: return
            event_type = f"TP{target_index}_HIT"
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type=event_type, event_data={"price": float(price)}))
            db_session.commit()
            rec_entity = self.repo._to_entity(rec)
            self.notify_reply(rec.id, f"🎯 Signal #{rec.asset} hit TP{target_index} at {price}!", db_session=db_session)
            await self.notify_card_update(rec_entity, db_session=db_session)
        await self.alert_service.build_triggers_index()

    def _get_or_create_system_user(self, db_session: Session) -> User:
        system_user = db_session.query(User).filter(User.telegram_user_id == -1).first()
        if not system_user:
            system_user = User(telegram_user_id=-1, username='system', user_type=UserType.ANALYST, is_active=True)
            db_session.add(system_user); db_session.flush()
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
            return RecommendationEntity(
                id=trade_orm.id, asset=Symbol(trade_orm.asset), side=Side(trade_orm.side), entry=Price(trade_orm.entry),
                stop_loss=Price(trade_orm.stop_loss), targets=Targets(trade_orm.targets),
                status=RecommendationStatusEntity.ACTIVE if trade_orm.status == UserTradeStatus.OPEN else RecommendationStatusEntity.CLOSED,
                order_type=OrderType.MARKET, created_at=trade_orm.created_at, closed_at=trade_orm.closed_at,
                exit_price=float(trade_orm.close_price) if trade_orm.close_price else None
            )
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