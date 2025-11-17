# File: src/capitalguard/infrastructure/db/repository.py
# Version: v2.11.1-R2 (AttributeError Hotfix)
# ✅ THE FIX: (R2 Architecture - Hotfix)
#    - 1. (CRITICAL) إصلاح `AttributeError: 'RecommendationRepository' object has no attribute '_to_entity_from_user_trade'`.
#    - 2. (NEW) إضافة دالة `_to_entity_from_user_trade` (المفقودة) كـ `staticmethod`.
#    - 3. (IMPORTS) إضافة الاستدعاءات الضرورية (UserTrade, Enums) لدعم الدالة الجديدة.
# 🎯 IMPACT: هذا الإصلاح يحل الـ `AttributeError` ويجعل `/myportfolio` قابلاً للعمل.

import logging
from typing import List, Optional, Any, Dict
from decimal import Decimal, InvalidOperation
from datetime import datetime # ✅ Added import

from sqlalchemy.orm import Session, joinedload, selectinload
import sqlalchemy as sa
from sqlalchemy import and_, or_, func, select, case

# Import domain entities and value objects
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType as OrderTypeEntity,
    ExitStrategy as ExitStrategyEntity,
    UserType as UserTypeEntity
)
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side

# Import ORM models
from .models import (
    User, Channel, Recommendation, RecommendationEvent,
    PublishedMessage, UserTrade, # ✅ Added UserTrade
    RecommendationStatusEnum,
    UserTradeStatusEnum, # ✅ Added UserTradeStatusEnum
    OrderTypeEnum,
    WatchedChannel,
    ParsingTemplate, ParsingAttempt
)

logger = logging.getLogger(__name__)

# ==========================================================
# USER REPOSITORY
# ==========================================================
class UserRepository:
    """Repository for User entities."""
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Finds a user by their Telegram ID."""
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_by_id(self, user_id: int) -> Optional[User]:
        """Finds a user by their internal database ID."""
        return self.session.query(User).filter(User.id == user_id).first()

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        """Finds a user by Telegram ID or creates a new one if not found."""
        user = self.find_by_telegram_id(telegram_id)
        if user:
            # Update user info if changed
            updated = False
            if kwargs.get("first_name") and user.first_name != kwargs["first_name"]:
                 user.first_name = kwargs["first_name"]
                 updated = True
            if kwargs.get("username") and user.username != kwargs["username"]:
                user.username = kwargs["username"]
                updated = True
            if 'user_type' in kwargs and user.user_type != kwargs['user_type']:
                 user.user_type = kwargs['user_type']
                 updated = True
            if 'is_active' in kwargs and user.is_active != kwargs['is_active']:
                user.is_active = kwargs['is_active']
                updated = True
            
            if updated:
                 self.session.flush() # Persist updates if any
            return user

        logger.info("Creating new user for telegram_id=%s", telegram_id)
        new_user = User(
            telegram_user_id=telegram_id,
            first_name=kwargs.get("first_name"),
            username=kwargs.get("username"),
            is_active=kwargs.get("is_active", False), # Default to inactive
            user_type=kwargs.get("user_type", UserTypeEntity.TRADER), # Use domain enum
        )
        self.session.add(new_user)
        self.session.flush() # Flush to get new_user.id if needed
        return new_user

# ==========================================================
# (ChannelRepository & ParsingRepository remain unchanged)
# ==========================================================
class ChannelRepository:
    """Repository for Channel entities."""
    def __init__(self, session: Session):
         self.session = session

    def find_by_telegram_id_and_analyst(self, channel_id: int, analyst_id: int) -> Optional[Channel]:
        """Finds a channel by its Telegram ID and owner analyst's ID."""
        return self.session.query(Channel).filter(
            Channel.telegram_channel_id == channel_id,
            Channel.analyst_id == analyst_id
        ).one_or_none()

    def list_by_analyst(self, analyst_id: int, only_active: bool = True) -> List[Channel]:
        """Lists channels linked to a specific analyst."""
        query = self.session.query(Channel).filter(Channel.analyst_id == analyst_id)
        if only_active:
            query = query.filter(Channel.is_active == True)
        return query.order_by(Channel.created_at.desc()).all()

    def add(self, analyst_id: int, telegram_channel_id: int, username: Optional[str], title: Optional[str]) -> Channel:
        """Adds a new channel linked to an analyst."""
        new_channel = Channel(
            analyst_id=analyst_id,
            telegram_channel_id=telegram_channel_id,
            username=username,
            title=title,
            is_active=True, # Default to active when added
        )
        self.session.add(new_channel)
        self.session.flush()
        return new_channel

    def delete(self, channel: Channel):
        """Deletes a channel record."""
        self.session.delete(channel)
        self.session.flush()

class ParsingRepository:
    """Repository for ParsingTemplate and ParsingAttempt entities."""
    def __init__(self, session: Session):
        self.session = session

    def add_attempt(self, **kwargs) -> ParsingAttempt:
        attempt = ParsingAttempt(**kwargs)
        self.session.add(attempt)
        self.session.flush()
        logger.debug(f"ParsingAttempt record created with ID: {attempt.id}")
        return attempt

    def update_attempt(self, attempt_id: int, **kwargs):
        logger.debug(f"Updating ParsingAttempt ID: {attempt_id} with data: {kwargs}")
        attempt = self.session.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
        if attempt:
            for key, value in kwargs.items():
                setattr(attempt, key, value)
            self.session.flush()
            logger.debug(f"ParsingAttempt ID: {attempt_id} updated successfully.")
        else:
            logger.warning(f"Attempted to update non-existent ParsingAttempt ID: {attempt_id}")

    def get_active_templates(self, user_id: Optional[int] = None) -> List[ParsingTemplate]:
        query = self.session.query(ParsingTemplate).filter(
            sa.or_(
                ParsingTemplate.is_public == True,
                ParsingTemplate.analyst_id == user_id
            )
        ).order_by(
            ParsingTemplate.confidence_score.desc().nullslast(),
            ParsingTemplate.id
        )
        templates = query.all()
        logger.debug(f"Fetched {len(templates)} active parsing templates for user_id={user_id}.")
        return templates

    def add_template(self, **kwargs) -> ParsingTemplate:
        kwargs.setdefault('is_public', False)
        template = ParsingTemplate(**kwargs)
        self.session.add(template)
        self.session.flush()
        logger.info(f"ParsingTemplate created with ID: {template.id} for analyst_id={template.analyst_id}")
        return template

    def find_template_by_id(self, template_id: int) -> Optional[ParsingTemplate]:
        return self.session.query(ParsingTemplate).filter(ParsingTemplate.id == template_id).first()

# ==========================================================
# RECOMMENDATION REPOSITORY (Updated for R2)
# ==========================================================
class RecommendationRepository:
    """Repository for Recommendation and UserTrade ORM models."""

    # ✅ R2: Helper to get the model class for type-safe queries
    def get_watched_channel_model(self) -> type[WatchedChannel]:
        return WatchedChannel

    @staticmethod
    def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
        if isinstance(value, Decimal):
             return value if value.is_finite() else default
        if value is None:
            return default
        try:
            d = Decimal(str(value))
            return d if d.is_finite() else default
        except (InvalidOperation, TypeError, ValueError):
            logger.debug(f"Could not convert '{value}' to Decimal, using default '{default}'.")
            return default

    @staticmethod
    def _to_entity(row: Recommendation) -> Optional[RecommendationEntity]:
        if not row: return None
        try:
            targets_data = row.targets or []
            formatted_targets = [
                {"price": RecommendationRepository._to_decimal(t.get("price")),
                 "close_percent": t.get("close_percent", 0.0)} 
                 for t in targets_data if t.get("price") is not None
            ]
            if not formatted_targets:
                 logger.warning(f"Recommendation {row.id} has no valid targets in JSON data: {row.targets}")
            entity = RecommendationEntity(
                id=row.id,
                analyst_id=row.analyst_id,
                asset=Symbol(row.asset),
                side=Side(row.side),
                entry=Price(RecommendationRepository._to_decimal(row.entry)),
                stop_loss=Price(RecommendationRepository._to_decimal(row.stop_loss)),
                targets=Targets(formatted_targets),
                order_type=OrderTypeEntity(row.order_type.value),
                status=RecommendationStatusEntity(row.status.value),
                market=row.market,
                notes=row.notes,
                created_at=row.created_at,
                updated_at=row.updated_at,
                exit_price=float(row.exit_price) if row.exit_price is not None else None,
                activated_at=row.activated_at,
                closed_at=row.closed_at,
                open_size_percent=float(row.open_size_percent),
                is_shadow=row.is_shadow,
                events=list(row.events or []),
                exit_strategy=ExitStrategyEntity(row.exit_strategy.value),
            )
            if hasattr(row, 'profit_stop_active'):
                 setattr(entity, 'profit_stop_active', row.profit_stop_active)
                 setattr(entity, 'profit_stop_mode', row.profit_stop_mode)
                 setattr(entity, 'profit_stop_price', RecommendationRepository._to_decimal(row.profit_stop_price) if row.profit_stop_price is not None else None)
                 setattr(entity, 'profit_stop_trailing_value', RecommendationRepository._to_decimal(row.profit_stop_trailing_value) if row.profit_stop_trailing_value is not None else None)
            return entity
        except Exception as e:
            logger.error(f"Error translating ORM Recommendation ID {getattr(row, 'id', 'N/A')} to entity: {e}", exc_info=True)
            return None

    # ✅✅✅ [FIX 2] HOTFIX: Added the missing helper function `_to_entity_from_user_trade`
    @staticmethod
    def _to_entity_from_user_trade(trade: UserTrade) -> Optional[RecommendationEntity]:
        """
        Converts a UserTrade ORM object into a RecommendationEntity-like object
        for unified display in handlers.
        """
        if not trade: 
            return None
        try:
            # Map UserTradeStatus to RecommendationStatus for display
            if trade.status == UserTradeStatusEnum.CLOSED:
                domain_status = RecommendationStatusEntity.CLOSED
            elif trade.status == UserTradeStatusEnum.ACTIVATED:
                domain_status = RecommendationStatusEntity.ACTIVE
            else: # WATCHLIST or PENDING_ACTIVATION
                domain_status = RecommendationStatusEntity.PENDING

            targets_data = trade.targets or []
            formatted_targets = [
                {"price": RecommendationRepository._to_decimal(t.get("price")),
                 "close_percent": t.get("close_percent", 0.0)} 
                 for t in targets_data if t.get("price") is not None
            ]

            trade_entity = RecommendationEntity(
                id=trade.id,
                asset=Symbol(trade.asset),
                side=Side(trade.side),
                entry=Price(RecommendationRepository._to_decimal(trade.entry)),
                stop_loss=Price(RecommendationRepository._to_decimal(trade.stop_loss)),
                targets=Targets(formatted_targets),
                status=domain_status,
                order_type=OrderTypeEntity.MARKET, # Default for user trades
                created_at=trade.created_at,
                closed_at=trade.closed_at,
                exit_price=float(trade.close_price) if trade.close_price is not None else None,
                exit_strategy=ExitStrategyEntity.MANUAL_CLOSE_ONLY, # Default
                analyst_id=trade.user_id # Use user_id as the "owner" context
            )
            
            # Add the critical attributes the UI relies on
            setattr(trade_entity, 'is_user_trade', True)
            setattr(trade_entity, 'orm_status_value', trade.status.value) 
            if trade.pnl_percentage is not None:
                setattr(trade_entity, 'final_pnl_percentage', float(trade.pnl_percentage))
            
            # Add fields needed by _send_or_edit_position_panel
            setattr(trade_entity, 'market', "Futures") # Assume futures default
            setattr(trade_entity, 'notes', None)
            setattr(trade_entity, 'activated_at', trade.activated_at)
            
            return trade_entity
        except Exception as e:
            logger.error(f"Error translating ORM UserTrade ID {getattr(trade, 'id', 'N/A')} to entity: {e}", exc_info=True)
            return None

    def get(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        return session.query(Recommendation).options(
            joinedload(Recommendation.analyst),
            selectinload(Recommendation.events) 
        ).filter(Recommendation.id == rec_id).first()

    def get_for_update(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        return session.query(Recommendation).filter(Recommendation.id == rec_id).with_for_update().first()

    def list_all_active_triggers_data(self, session: Session) -> List[Dict[str, Any]]:
        """
        Gets raw data (as dicts) for ALL active triggers:
        1. PENDING/ACTIVE Recommendations (and NOT shadow)
        2. WATCHLIST/PENDING_ACTIVATION/ACTIVATED UserTrades
        """
        trigger_data = []
        
        # 1. Fetch Recommendations
        active_recs = self.get_all_active_recs(session)
        for rec in active_recs:
            try:
                entry_dec = self._to_decimal(rec.entry)
                sl_dec = self._to_decimal(rec.stop_loss)
                targets_list = [
                     {"price": self._to_decimal(t.get("price")),
                      "close_percent": t.get("close_percent", 0.0)}
                     for t in (rec.targets or []) if t.get("price") is not None
                ]

                user_id_str = str(rec.analyst.telegram_user_id) if rec.analyst else None
                if not user_id_str:
                    logger.warning(f"Skipping trigger for Rec ID {rec.id}: Analyst relationship not loaded or user missing.")
                    continue

                data = {
                    "id": rec.id,
                    "item_type": "recommendation", 
                    "user_id": user_id_str, 
                    "user_db_id": rec.analyst_id, 
                    "asset": rec.asset,
                    "side": rec.side,
                    "entry": entry_dec,
                    "stop_loss": sl_dec,
                    "targets": targets_list, 
                    "status": rec.status, 
                    "order_type": rec.order_type, 
                    "market": rec.market,
                    "processed_events": {e.event_type for e in rec.events},
                    "profit_stop_mode": getattr(rec, 'profit_stop_mode', 'NONE'),
                    "profit_stop_price": self._to_decimal(getattr(rec, 'profit_stop_price', None)) if getattr(rec, 'profit_stop_price', None) is not None else None,
                    "profit_stop_trailing_value": self._to_decimal(getattr(rec, 'profit_stop_trailing_value', None)) if getattr(rec, 'profit_stop_trailing_value', None) is not None else None,
                    "profit_stop_active": getattr(rec, 'profit_stop_active', False),
                    "original_published_at": None, 
                }
                trigger_data.append(data)
            except Exception as e:
                logger.error(f"Failed to process trigger data for rec #{rec.id}: {e}", exc_info=True)

        # 2. Fetch UserTrades
        active_trades = self.get_all_active_user_trades(session)
        for trade in active_trades:
            try:
                entry_dec = self._to_decimal(trade.entry)
                sl_dec = self._to_decimal(trade.stop_loss)
                targets_list = [
                     {"price": self._to_decimal(t.get("price")),
                      "close_percent": t.get("close_percent", 0.0)}
                     for t in (trade.targets or []) if t.get("price") is not None
                ]

                user_id_str = str(trade.user.telegram_user_id) if trade.user else None
                if not user_id_str:
                    logger.warning(f"Skipping trigger for UserTrade ID {trade.id}: User relationship not loaded or user missing.")
                    continue

                data = {
                    "id": trade.id,
                    "item_type": "user_trade", 
                    "user_id": user_id_str, 
                    "user_db_id": trade.user_id, 
                    "asset": trade.asset,
                    "side": trade.side,
                    "entry": entry_dec,
                    "stop_loss": sl_dec,
                    "targets": targets_list, 
                    "status": trade.status, 
                    "order_type": OrderTypeEnum.LIMIT, 
                    "market": "Futures", 
                    "processed_events": {e.event_type for e in trade.events},
                    "profit_stop_mode": "NONE",
                    "profit_stop_price": None,
                    "profit_stop_trailing_value": None,
                    "profit_stop_active": False,
                    "original_published_at": trade.original_published_at,
                }
                trigger_data.append(data)
            except Exception as e:
                logger.error(f"Failed to process trigger data for user_trade #{trade.id}: {e}", exc_info=True)

        logger.info(f"Generated {len(trigger_data)} total active triggers (Recs + UserTrades).")
        return trigger_data


    def get_published_messages(self, session: Session, rec_id: int) -> List[PublishedMessage]:
        return session.query(PublishedMessage).filter(PublishedMessage.recommendation_id == rec_id).all()

    def get_open_recs_for_analyst(self, session: Session, analyst_user_id: int) -> List[Recommendation]:
        return session.query(Recommendation).filter(
            Recommendation.analyst_id == analyst_user_id,
            Recommendation.is_shadow.is_(False),
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE]),
        ).order_by(Recommendation.created_at.desc()).all()

    def get_open_trades_for_trader(self, session: Session, trader_user_id: int) -> List[UserTrade]:
        """
        Fetches all non-closed trades for a trader.
        This includes WATCHLIST, PENDING_ACTIVATION, and ACTIVATED.
        """
        return session.query(UserTrade).options(
            selectinload(UserTrade.watched_channel) # Eager load channel info
        ).filter(
            UserTrade.user_id == trader_user_id,
            UserTrade.status.in_([
                UserTradeStatusEnum.WATCHLIST, 
                UserTradeStatusEnum.PENDING_ACTIVATION,
                UserTradeStatusEnum.ACTIVATED
            ]),
        ).order_by(UserTrade.created_at.desc()).all()

    def get_user_trade_by_id(self, session: Session, trade_id: int) -> Optional[UserTrade]:
        return session.query(UserTrade).options(
            selectinload(UserTrade.events)
         ).filter(UserTrade.id == trade_id).first()

    def find_user_trade_by_source_id(self, session: Session, user_id: int, rec_id: int) -> Optional[UserTrade]:
        return session.query(UserTrade).filter(
            UserTrade.user_id == user_id,
            UserTrade.source_recommendation_id == rec_id,
            UserTrade.status.in_([
                UserTradeStatusEnum.WATCHLIST, 
                UserTradeStatusEnum.PENDING_ACTIVATION,
                UserTradeStatusEnum.ACTIVATED
            ])
        ).first()

    def get_events_for_recommendation(self, session: Session, rec_id: int) -> List[RecommendationEvent]:
        return session.query(RecommendationEvent).filter(
            RecommendationEvent.recommendation_id == rec_id
        ).order_by(RecommendationEvent.event_timestamp.asc()).all()

    def get_all_active_recs(self, session: Session) -> List[Recommendation]:
        return session.query(Recommendation).options(
            selectinload(Recommendation.events), 
            joinedload(Recommendation.analyst) 
        ).filter(
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE]),
            Recommendation.is_shadow.is_(False) # Ignore "publishing" items
        ).all()

    def get_all_active_user_trades(self, session: Session) -> List[UserTrade]:
        """Fetches all active user trades with user and events preloaded."""
        return session.query(UserTrade).options(
            joinedload(UserTrade.user),
            selectinload(UserTrade.events) # Eager load events
        ).filter(
            UserTrade.status.in_([
                UserTradeStatusEnum.WATCHLIST, 
                UserTradeStatusEnum.PENDING_ACTIVATION,
                UserTradeStatusEnum.ACTIVATED
            ])
        ).all()

    def get_active_recs_for_asset_and_market(self, session: Session, asset: str, market: str) -> List[Recommendation]:
        asset_upper = asset.strip().upper()
        return session.query(Recommendation).filter(
            and_( 
                Recommendation.asset == asset_upper,
                Recommendation.market == market, 
                Recommendation.status == RecommendationStatusEnum.ACTIVE,
                Recommendation.is_shadow.is_(False)
            )
        ).all()

    # ✅ NEW (R2): Function for "Design 5" (By Channel)
    def get_watched_channels_summary(self, session: Session, user_id: int) -> List[Dict[str, Any]]:
        """
        [R2 - Core Algorithm]
        Fetches all channels a user is watching (forwarded from)
        and counts *only* their NON-CLOSED trades in each.
        """
        try:
            # 1. Define active trade statuses
            active_statuses = [
                UserTradeStatusEnum.ACTIVATED,
                UserTradeStatusEnum.PENDING_ACTIVATION,
                UserTradeStatusEnum.WATCHLIST
            ]

            # 2. Subquery to count active trades per channel
            subquery = (
                select(
                    UserTrade.watched_channel_id,
                    func.count(UserTrade.id).label("active_trade_count")
                )
                .where(
                    UserTrade.user_id == user_id,
                    UserTrade.status.in_(active_statuses)
                )
                .group_by(UserTrade.watched_channel_id)
                .subquery()
            )

            # 3. Main query to join WatchedChannel with the counts
            stmt = (
                select(
                    WatchedChannel.id,
                    WatchedChannel.channel_title,
                    WatchedChannel.telegram_channel_id,
                    func.coalesce(subquery.c.active_trade_count, 0).label("active_trade_count")
                )
                .join(
                    subquery,
                    subquery.c.watched_channel_id == WatchedChannel.id,
                    isouter=True # Use LEFT JOIN to include channels with 0 trades
                )
                .where(
                    WatchedChannel.user_id == user_id,
                    WatchedChannel.is_active == True
                )
                .order_by(WatchedChannel.channel_title)
            )
            
            results = self.session.execute(stmt).all()
            
            # 4. Count trades with NO channel (Direct Input)
            direct_input_count_stmt = (
                select(func.count(UserTrade.id))
                .where(
                    UserTrade.user_id == user_id,
                    UserTrade.status.in_(active_statuses),
                    UserTrade.watched_channel_id.is_(None)
                )
            )
            direct_input_count = self.session.execute(direct_input_count_stmt).scalar() or 0
            
            # Format results
            summary_list = [{"id": r.id, "title": r.channel_title, "count": r.active_trade_count} for r in results]
            
            if direct_input_count > 0:
                summary_list.append({"id": "direct", "title": "Direct Input", "count": direct_input_count})
                
            return summary_list

        except Exception as e:
            logger.error(f"Error fetching watched channels summary for user {user_id}: {e}", exc_info=True)
            return []