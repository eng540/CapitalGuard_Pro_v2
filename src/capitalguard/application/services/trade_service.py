# src/capitalguard/application/services/trade_service.py (v25.6 - FINAL & COMPLETE)
"""
TradeService - The re-architected, event-driven, and robust core of the system.
This version is designed for reliability, atomicity, and extensibility, and includes
all necessary helper methods that were previously missing.
"""

# --- STAGE 1 & 2: ANALYSIS & BLUEPRINT ---
# Core Purpose: To be the single entry point for all business operations related to the
# lifecycle of recommendations and user trades. It encapsulates and protects the core
# business logic of the application.
#
# Behavior:
#   Input: A request from an interface layer (e.g., Telegram handler, API endpoint)
#          with user context and data.
#   Process:
#     1. Authorize the action (e.g., is the user an analyst?).
#     2. Validate the business rules (e.g., is the SL valid for a LONG trade?).
#     3. Perform the state change within a single atomic database transaction (@uow_transaction).
#        - Modify the state of the ORM model.
#        - Create a corresponding `RecommendationEvent` to log the change.
#     4. After the transaction commits, trigger side effects (e.g., notify Telegram,
#        request an `AlertService` index rebuild).
#   Output: A domain entity representing the new state of the object.
#
# Dependencies:
#   - `RecommendationRepository`: To interact with the database.
#   - `Notifier`: To send messages to external systems (Telegram).
#   - `MarketDataService`, `PriceService`: To fetch market data.
#   - `AlertService`: To signal that the monitoring index needs to be updated.
#
# Essential Functions:
#   - `create_and_publish_recommendation_async`: The main entry point for new signals.
#   - `create_trade_from_forwarding`: Handles the "track forwarded signal" feature.
#   - `create_trade_from_recommendation`: Handles the "deep link tracking" feature.
#   - `get_recent_assets_for_user`: The previously missing helper function. CRITICAL FIX.
#   - Event Processors (`process_activation_event`, etc.): Atomic state change handlers.
#   - User-facing management functions (`cancel_pending...`, `close_recommendation...`, etc.).
#
# Blueprint:
#   - `TradeService` class:
#     - `__init__`: Initialize dependencies.
#     - Helper methods (`_call_notifier...`, `_get_or_create_system_user`).
#     - Core `_validate_recommendation_data` method with strict business rules.
#     - Public methods for creating trades/recommendations.
#     - Public methods for processing events triggered by `AlertService`.
#     - Public methods for handling manual user actions.
#     - Public read-only methods for fetching data (`get_open_positions...`, `get_recent_assets...`).
#     - All write operations MUST be decorated with `@uow_transaction`.

# --- STAGE 3: FULL CONSTRUCTION ---

import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import uow_transaction
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

    async def notify_card_update(self, rec_entity: RecommendationEntity):
        """Sends an update to all published Telegram messages for a recommendation."""
        if rec_entity.is_shadow: return
        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec_entity.id)
            if not published_messages: return
            for msg_meta in published_messages:
                try:
                    await self._call_notifier_maybe_async(
                        self.notifier.edit_recommendation_card_by_ids, 
                        channel_id=msg_meta.telegram_channel_id, 
                        message_id=msg_meta.telegram_message_id, 
                        rec=rec_entity
                    )
                except Exception as e:
                    logger.error("Failed to update card for rec %s in channel %s: %s", 
                               rec_entity.id, msg_meta.telegram_channel_id, e)

    def notify_reply(self, rec_id: int, text: str):
        """Posts a reply to all published messages for a recommendation."""
        with session_scope() as session:
            rec = self.repo.get(session, rec_id)
            if not rec or rec.is_shadow: return
            published_messages = self.repo.get_published_messages(session, rec_id)
            for msg_meta in published_messages:
                try:
                    asyncio.create_task(self._call_notifier_maybe_async(
                        self.notifier.post_notification_reply, 
                        chat_id=msg_meta.telegram_channel_id, 
                        message_id=msg_meta.telegram_message_id, 
                        text=text
                    ))
                except Exception as e:
                    logger.warning("Failed to send reply for rec #%s to channel %s: %s", 
                                 rec_id, msg_meta.telegram_channel_id, e)

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
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(session).find_by_telegram_id(uid_int)
        if not user:
            report["failed"].append({"reason": "User not found"})
            return rec_entity, report
        channels_to_publish = ChannelRepository(session).list_by_analyst(user.id, only_active=True)
        if target_channel_ids is not None:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
        if not channels_to_publish:
            report["failed"].append({"reason": "No active channels linked."})
            return rec_entity, report
        
        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        
        for channel in channels_to_publish:
            try:
                result = await self._call_notifier_maybe_async(
                    self.notifier.post_to_channel, channel.telegram_channel_id, rec_entity, keyboard
                )
                if isinstance(result, tuple) and len(result) == 2:
                    publication = PublishedMessage(
                        recommendation_id=rec_entity.id,
                        telegram_channel_id=result[0],
                        telegram_message_id=result[1]
                    )
                    session.add(publication)
                    report["success"].append({"channel_id": channel.telegram_channel_id, "message_id": result[1]})
                else:
                    raise RuntimeError(f"Notifier returned unsupported response type: {type(result)}")
            except Exception as e:
                report["failed"].append({"channel_id": channel.telegram_channel_id, "reason": str(e)})
        session.flush()
        return rec_entity, report

    @uow_transaction
    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """Creates and publishes a new recommendation within a single atomic transaction."""
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(db_session).find_by_telegram_id(uid_int)
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can create recommendations.")

        entry_price = Decimal(str(kwargs['entry']))
        sl_price = Decimal(str(kwargs['stop_loss']))
        targets_list = [{'price': Decimal(str(t['price'])), 'close_percent': t.get('close_percent', 0)} for t in kwargs['targets']]
        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        order_type_enum = OrderType(kwargs['order_type'].upper())
        
        status, final_entry = RecommendationStatusEnum.PENDING, entry_price
        if order_type_enum == OrderTypeEnum.MARKET:
            live_price_float = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            if live_price_float is None:
                raise RuntimeError(f"Could not fetch live price for {asset}.")
            status, final_entry = RecommendationStatusEnum.ACTIVE, Decimal(str(live_price_float))

        self._validate_recommendation_data(side, final_entry, sl_price, targets_list)

        rec_orm = Recommendation(
            analyst_id=user.id, asset=asset, side=side, entry=final_entry, stop_loss=sl_price,
            targets=targets_list, order_type=order_type_enum, status=status, market=market,
            notes=kwargs.get('notes'), exit_strategy=ExitStrategyEnum[kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP).value],
            is_shadow=False,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )
        db_session.add(rec_orm)
        db_session.flush()

        event_type = "CREATED_ACTIVE" if rec_orm.status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING"
        db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={}))
        db_session.flush()
        db_session.refresh(rec_orm)

        created_rec_entity = self.repo._to_entity(rec_orm)
        final_rec, report = await self._publish_recommendation(db_session, created_rec_entity, user_id, kwargs.get('target_channel_ids'))
        
        await self.alert_service.build_triggers_index()
        return final_rec, report

    @uow_transaction
    async def create_trade_from_forwarding(self, user_id: str, trade_data: Dict[str, Any], db_session: Session) -> Dict[str, Any]:
        """Creates a UserTrade and a corresponding Shadow Recommendation for tracking."""
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}

        system_user = self._get_or_create_system_user(db_session)

        shadow_rec = Recommendation(
            analyst_id=system_user.id, asset=trade_data['asset'], side=trade_data['side'],
            entry=Decimal(str(trade_data['entry'])), stop_loss=Decimal(str(trade_data['stop_loss'])),
            targets=[{'price': Decimal(str(t['price'])), 'close_percent': t.get('close_percent', 0)} for t in trade_data['targets']],
            status=RecommendationStatusEnum.ACTIVE, order_type=OrderTypeEnum.MARKET,
            notes="Shadow recommendation from forwarded user trade.", market="Futures",
            is_shadow=True, activated_at=datetime.now(timezone.utc)
        )
        db_session.add(shadow_rec)
        db_session.flush()

        new_trade = UserTrade(
            user_id=trader_user.id, source_recommendation_id=shadow_rec.id,
            asset=trade_data['asset'], side=trade_data['side'],
            entry=Decimal(str(trade_data['entry'])), stop_loss=Decimal(str(trade_data['stop_loss'])),
            targets=[{'price': Decimal(str(t['price'])), 'close_percent': t.get('close_percent', 0)} for t in trade_data['targets']],
            status=UserTradeStatus.OPEN
        )
        db_session.add(new_trade)
        db_session.flush()

        await self.alert_service.build_triggers_index()
        return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}

    @uow_transaction
    async def create_trade_from_recommendation(self, user_id: str, rec_id: int, db_session: Session) -> Dict[str, Any]:
        """Creates a UserTrade for a user who wants to track an official recommendation."""
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}

        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm:
            return {'success': False, 'error': 'Recommendation not found'}

        existing_trade = db_session.query(UserTrade).filter(
            UserTrade.user_id == trader_user.id, UserTrade.source_recommendation_id == rec_id
        ).first()
        if existing_trade:
            return {'success': False, 'error': 'You are already tracking this signal.'}

        new_trade = UserTrade(
            user_id=trader_user.id, source_recommendation_id=rec_orm.id,
            asset=rec_orm.asset, side=rec_orm.side, entry=rec_orm.entry,
            stop_loss=rec_orm.stop_loss, targets=rec_orm.targets, status=UserTradeStatus.OPEN
        )
        db_session.add(new_trade)
        db_session.flush()

        await self.alert_service.build_triggers_index()
        return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}

    @uow_transaction
    async def process_invalidation_event(self, item_id: int, db_session: Session):
        """Handles the business logic for when a PENDING recommendation's SL is hit."""
        rec = db_session.query(Recommendation).filter(Recommendation.id == item_id).with_for_update().first()
        if not rec or rec.status != RecommendationStatusEnum.PENDING:
            return
        
        rec.status = RecommendationStatusEnum.CLOSED
        rec.closed_at = datetime.now(timezone.utc)
        db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED", event_data={"reason": "SL hit before entry"}))
        
        await self.alert_service.build_triggers_index()
        rec_entity = self.repo._to_entity(rec)
        self.notify_reply(rec.id, f"❌ Signal #{rec.asset} was invalidated (SL hit before entry).")
        await self.notify_card_update(rec_entity)

    @uow_transaction
    async def process_activation_event(self, item_id: int, db_session: Session):
        """Handles the business logic for when a PENDING recommendation is activated."""
        rec = db_session.query(Recommendation).filter(Recommendation.id == item_id).with_for_update().first()
        if not rec or rec.status != RecommendationStatusEnum.PENDING:
            return

        rec.status = RecommendationStatusEnum.ACTIVE
        rec.activated_at = datetime.now(timezone.utc)
        db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))

        await self.alert_service.build_triggers_index()
        rec_entity = self.repo._to_entity(rec)
        self.notify_reply(rec.id, f"▶️ Signal #{rec.asset} is now ACTIVE!")
        await self.notify_card_update(rec_entity)

    @uow_transaction
    async def process_sl_hit_event(self, item_id: int, price: Decimal, db_session: Session):
        """Handles the business logic for when an ACTIVE item's SL is hit."""
        rec = db_session.query(Recommendation).filter(Recommendation.id == item_id).with_for_update().first()
        if not rec or rec.status != RecommendationStatusEnum.ACTIVE:
            return

        rec.status = RecommendationStatusEnum.CLOSED
        rec.closed_at = datetime.now(timezone.utc)
        rec.exit_price = price
        db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="SL_HIT", event_data={"price": float(price)}))

        await self.alert_service.build_triggers_index()
        rec_entity = self.repo._to_entity(rec)
        self.notify_reply(rec.id, f"🛑 Signal #{rec.asset} hit Stop Loss at {price}.")
        await self.notify_card_update(rec_entity)

    @uow_transaction
    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal, db_session: Session):
        """Handles the business logic for when an ACTIVE item's TP is hit."""
        rec = db_session.query(Recommendation).filter(Recommendation.id == item_id).with_for_update().first()
        if not rec or rec.status != RecommendationStatusEnum.ACTIVE:
            return

        event_type = f"TP{target_index}_HIT"
        db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type=event_type, event_data={"price": float(price)}))
        
        await self.alert_service.build_triggers_index()
        rec_entity = self.repo._to_entity(rec)
        self.notify_reply(rec.id, f"🎯 Signal #{rec.asset} hit TP{target_index} at {price}!")
        await self.notify_card_update(rec_entity)

    def _get_or_create_system_user(self, db_session: Session) -> User:
        """Finds or creates a system user for automated tasks."""
        system_user = db_session.query(User).filter(User.telegram_user_id == -1).first()
        if not system_user:
            system_user = User(
                telegram_user_id=-1, 
                username='system', 
                user_type=UserType.ANALYST, 
                is_active=True
            )
            db_session.add(system_user)
            db_session.flush()
        return system_user

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """Gets all open positions (both recommendations and trades) for a user."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: return []
        
        open_positions = []
        
        if user.user_type == UserType.ANALYST:
            recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in recs_orm:
                rec_entity = self.repo._to_entity(rec)
                if rec_entity:
                    setattr(rec_entity, 'is_user_trade', False)
                    open_positions.append(rec_entity)

        trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trades_orm:
            trade_entity = RecommendationEntity(
                id=trade.id, asset=Symbol(trade.asset), side=Side(trade.side),
                entry=Price(trade.entry), stop_loss=Price(trade.stop_loss),
                targets=Targets(trade.targets), status=RecommendationStatusEntity.ACTIVE,
                order_type=OrderType.MARKET,
                created_at=trade.created_at
            )
            setattr(trade_entity, 'is_user_trade', True)
            open_positions.append(trade_entity)
            
        open_positions.sort(key=lambda p: p.created_at, reverse=True)
        return open_positions

    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """Gets the detailed entity for a single position, handling permissions."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: return None

        if position_type == 'rec':
            if user.user_type != UserType.ANALYST: return None
            rec_orm = self.repo.get(db_session, position_id)
            if not rec_orm or rec_orm.analyst_id != user.id: return None
            rec_entity = self.repo._to_entity(rec_orm)
            if rec_entity: setattr(rec_entity, 'is_user_trade', False)
            return rec_entity
        
        elif position_type == 'trade':
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if not trade_orm or trade_orm.user_id != user.id: return None
            
            trade_entity = RecommendationEntity(
                id=trade_orm.id, asset=Symbol(trade_orm.asset), side=Side(trade_orm.side),
                entry=Price(trade_orm.entry), stop_loss=Price(trade_orm.stop_loss),
                targets=Targets(trade_orm.targets),
                status=RecommendationStatusEntity.ACTIVE if trade_orm.status == UserTradeStatus.OPEN else RecommendationStatusEntity.CLOSED,
                order_type=OrderType.MARKET,
                created_at=trade_orm.created_at, closed_at=trade_orm.closed_at,
                exit_price=float(trade_orm.close_price) if trade_orm.close_price else None
            )
            setattr(trade_entity, 'is_user_trade', True)
            return trade_entity
            
        return None

    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """
        Fetches recently used assets for a user to populate UI keyboards.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user:
            return []
        
        if user.user_type == UserType.ANALYST:
            recs = self.repo.get_open_recs_for_analyst(db_session, user.id)
            assets = list(dict.fromkeys([r.asset for r in recs]))[:limit]
        else:
            trades = self.repo.get_open_trades_for_trader(db_session, user.id)
            assets = list(dict.fromkeys([t.asset for t in trades]))[:limit]
            
        if not assets:
            return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
        return assets

#END