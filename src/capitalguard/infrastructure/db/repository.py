#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/repository.py ---
# File: src/capitalguard/infrastructure/db/repository.py
# Version: v3.0.0-R3 (Status Normalization Layer)
# ✅ THE FIX: Added centralized 'normalize_status' methods.
#    - All DB reads now pass through normalization before becoming Entities.
#    - Prevents crashes even if DB contains legacy values like 'STOPPED'.
# 🎯 IMPACT: System resilience against Data Drift.

import logging
from typing import List, Optional, Any, Dict
from decimal import Decimal, InvalidOperation
from datetime import datetime 

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
    PublishedMessage, UserTrade, 
    RecommendationStatusEnum,
    UserTradeStatusEnum,
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
# RECOMMENDATION REPOSITORY (NORMALIZATION ENFORCED)
# ==========================================================
class RecommendationRepository:
    """Repository for Recommendation and UserTrade ORM models with Status Normalization."""

    def get_watched_channel_model(self) -> type[WatchedChannel]:
        return WatchedChannel

    # --- ✅ NORMALIZATION LAYER (The Core Fix) ---
    @staticmethod
    def normalize_recommendation_status(status_raw: Any) -> RecommendationStatusEntity:
        """
        Centralized logic to map ANY database value to a valid Domain Enum.
        Handles strings, Enums, and legacy values safely.
        """
        if isinstance(status_raw, RecommendationStatusEntity):
            return status_raw
        
        # Convert to string and normalize
        s = str(status_raw.value if hasattr(status_raw, 'value') else status_raw).upper().strip()
        
        # Explicit Mapping
        if s == "PENDING": return RecommendationStatusEntity.PENDING
        if s == "ACTIVE": return RecommendationStatusEntity.ACTIVE
        if s == "CLOSED": return RecommendationStatusEntity.CLOSED
        
        # Legacy/Garbage Handling -> Default to CLOSED
        logger.warning(f"⚠️ Normalizing unknown Recommendation status '{s}' to CLOSED.")
        return RecommendationStatusEntity.CLOSED

    @staticmethod
    def normalize_user_trade_status(status_raw: Any) -> UserTradeStatusEnum:
        """
        Centralized logic to map ANY database value to a valid UserTrade Enum.
        """
        if isinstance(status_raw, UserTradeStatusEnum):
            return status_raw
            
        s = str(status_raw.value if hasattr(status_raw, 'value') else status_raw).upper().strip()
        
        if s == "WATCHLIST": return UserTradeStatusEnum.WATCHLIST
        if s == "PENDING_ACTIVATION": return UserTradeStatusEnum.PENDING_ACTIVATION
        if s == "ACTIVATED": return UserTradeStatusEnum.ACTIVATED
        if s == "CLOSED": return UserTradeStatusEnum.CLOSED
        
        # Legacy/Garbage Handling -> Default to CLOSED
        logger.warning(f"⚠️ Normalizing unknown UserTrade status '{s}' to CLOSED.")
        return UserTradeStatusEnum.CLOSED
    # ---------------------------------------------

    @staticmethod
    def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
        if isinstance(value, Decimal): return value if value.is_finite() else default
        if value is None: return default
        try: return Decimal(str(value)) if Decimal(str(value)).is_finite() else default
        except: return default

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
                 logger.warning(f"Recommendation {row.id} has no valid targets. Skipping.")
                 return None

            # ✅ USE NORMALIZATION
            safe_status = RecommendationRepository.normalize_recommendation_status(row.status)

            entity = RecommendationEntity(
                id=row.id,
                analyst_id=row.analyst_id,
                asset=Symbol(row.asset),
                side=Side(row.side),
                entry=Price(RecommendationRepository._to_decimal(row.entry)),
                stop_loss=Price(RecommendationRepository._to_decimal(row.stop_loss)),
                targets=Targets(formatted_targets),
                order_type=OrderTypeEntity(row.order_type.value),
                status=safe_status, # Injected safe status
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
            # Attach extra fields for logic
            if hasattr(row, 'profit_stop_active'):
                 setattr(entity, 'profit_stop_active', row.profit_stop_active)
                 setattr(entity, 'profit_stop_mode', row.profit_stop_mode)
                 setattr(entity, 'profit_stop_price', RecommendationRepository._to_decimal(row.profit_stop_price) if row.profit_stop_price is not None else None)
                 setattr(entity, 'profit_stop_trailing_value', RecommendationRepository._to_decimal(row.profit_stop_trailing_value) if row.profit_stop_trailing_value is not None else None)
            return entity
        except Exception as e:
            logger.error(f"Error translating ORM Recommendation ID {getattr(row, 'id', 'N/A')}: {e}", exc_info=True)
            return None

    @staticmethod
    def _to_entity_from_user_trade(trade: UserTrade) -> Optional[RecommendationEntity]:
        if not trade: return None
        try:
            # ✅ USE NORMALIZATION
            safe_trade_status = RecommendationRepository.normalize_user_trade_status(trade.status)

            # Map UserTradeStatus to RecommendationStatus for display
            if safe_trade_status == UserTradeStatusEnum.CLOSED:
                domain_status = RecommendationStatusEntity.CLOSED
            elif safe_trade_status == UserTradeStatusEnum.ACTIVATED:
                domain_status = RecommendationStatusEntity.ACTIVE
            else: 
                domain_status = RecommendationStatusEntity.PENDING

            targets_data = trade.targets or []
            formatted_targets = [
                {"price": RecommendationRepository._to_decimal(t.get("price")),
                 "close_percent": t.get("close_percent", 0.0)} 
                 for t in targets_data if t.get("price") is not None
            ]

            if not formatted_targets:
                logger.warning(f"UserTrade {trade.id} has no valid targets. Skipping.")
                return None

            trade_entity = RecommendationEntity(
                id=trade.id,
                asset=Symbol(trade.asset),
                side=Side(trade.side),
                entry=Price(RecommendationRepository._to_decimal(trade.entry)),
                stop_loss=Price(RecommendationRepository._to_decimal(trade.stop_loss)),
                targets=Targets(formatted_targets),
                status=domain_status,
                order_type=OrderTypeEntity.MARKET,
                created_at=trade.created_at,
                closed_at=trade.closed_at,
                exit_price=float(trade.close_price) if trade.close_price is not None else None,
                exit_strategy=ExitStrategyEntity.MANUAL_CLOSE_ONLY,
                analyst_id=trade.user_id
            )
            
            setattr(trade_entity, 'is_user_trade', True)
            setattr(trade_entity, 'orm_status_value', safe_trade_status.value) # Use normalized value
            if trade.pnl_percentage is not None:
                setattr(trade_entity, 'final_pnl_percentage', float(trade.pnl_percentage))
            
            setattr(trade_entity, 'market', "Futures")
            setattr(trade_entity, 'notes', None)
            setattr(trade_entity, 'activated_at', trade.activated_at)
            
            return trade_entity
        except Exception as e:
            logger.error(f"Error translating ORM UserTrade ID {getattr(trade, 'id', 'N/A')}: {e}", exc_info=True)
            return None

    def get(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        return session.query(Recommendation).options(joinedload(Recommendation.analyst), selectinload(Recommendation.events)).filter(Recommendation.id == rec_id).first()

    def get_for_update(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        return session.query(Recommendation).filter(Recommendation.id == rec_id).with_for_update().first()

    def list_all_active_triggers_data(self, session: Session) -> List[Dict[str, Any]]:
        # This method returns raw dicts for the engine.
        # It queries based on Enum values. Since we sanitized DB, this is safe.
        trigger_data = []
        
        # 1. Recommendations
        active_recs = session.query(Recommendation).options(selectinload(Recommendation.events), joinedload(Recommendation.analyst)).filter(
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE]),
            Recommendation.is_shadow.is_(False)
        ).all()
        
        for rec in active_recs:
            try:
                entry_dec = self._to_decimal(rec.entry)
                sl_dec = self._to_decimal(rec.stop_loss)
                targets_list = [{"price": self._to_decimal(t.get("price")), "close_percent": t.get("close_percent", 0.0)} for t in (rec.targets or []) if t.get("price") is not None]
                if not targets_list: continue
                user_id_str = str(rec.analyst.telegram_user_id) if rec.analyst else None
                if not user_id_str: continue

                data = {
                    "id": rec.id, "item_type": "recommendation", "user_id": user_id_str, "user_db_id": rec.analyst_id,
                    "asset": rec.asset, "side": rec.side, "entry": entry_dec, "stop_loss": sl_dec, "targets": targets_list,
                    "status": rec.status, "order_type": rec.order_type, "market": rec.market,
                    "processed_events": {e.event_type for e in rec.events},
                    "profit_stop_mode": getattr(rec, 'profit_stop_mode', 'NONE'),
                    "profit_stop_price": self._to_decimal(getattr(rec, 'profit_stop_price', None)),
                    "profit_stop_trailing_value": self._to_decimal(getattr(rec, 'profit_stop_trailing_value', None)),
                    "profit_stop_active": getattr(rec, 'profit_stop_active', False),
                    "original_published_at": None,
                }
                trigger_data.append(data)
            except Exception as e: logger.error(f"Trigger data error Rec {rec.id}: {e}")

        # 2. UserTrades
        active_trades = session.query(UserTrade).options(joinedload(UserTrade.user), selectinload(UserTrade.events)).filter(
            UserTrade.status.in_([UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.ACTIVATED])
        ).all()

        for trade in active_trades:
            try:
                entry_dec = self._to_decimal(trade.entry)
                sl_dec = self._to_decimal(trade.stop_loss)
                targets_list = [{"price": self._to_decimal(t.get("price")), "close_percent": t.get("close_percent", 0.0)} for t in (trade.targets or []) if t.get("price") is not None]
                if not targets_list: continue
                user_id_str = str(trade.user.telegram_user_id) if trade.user else None
                if not user_id_str: continue

                data = {
                    "id": trade.id, "item_type": "user_trade", "user_id": user_id_str, "user_db_id": trade.user_id,
                    "asset": trade.asset, "side": trade.side, "entry": entry_dec, "stop_loss": sl_dec, "targets": targets_list,
                    "status": trade.status, "order_type": OrderTypeEnum.LIMIT, "market": "Futures",
                    "processed_events": {e.event_type for e in trade.events},
                    "profit_stop_mode": "NONE", "profit_stop_price": None, "profit_stop_trailing_value": None, "profit_stop_active": False,
                    "original_published_at": trade.original_published_at,
                }
                trigger_data.append(data)
            except Exception as e: logger.error(f"Trigger data error Trade {trade.id}: {e}")

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
        return session.query(UserTrade).options(selectinload(UserTrade.watched_channel)).filter(
            UserTrade.user_id == trader_user_id,
            UserTrade.status.in_([UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.ACTIVATED]),
        ).order_by(UserTrade.created_at.desc()).all()

    def get_user_trade_by_id(self, session: Session, trade_id: int) -> Optional[UserTrade]:
        return session.query(UserTrade).options(selectinload(UserTrade.events)).filter(UserTrade.id == trade_id).first()

    def find_user_trade_by_source_id(self, session: Session, user_id: int, rec_id: int) -> Optional[UserTrade]:
        return session.query(UserTrade).filter(
            UserTrade.user_id == user_id, UserTrade.source_recommendation_id == rec_id,
            UserTrade.status.in_([UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.ACTIVATED])
        ).first()

    def get_events_for_recommendation(self, session: Session, rec_id: int) -> List[RecommendationEvent]:
        return session.query(RecommendationEvent).filter(RecommendationEvent.recommendation_id == rec_id).order_by(RecommendationEvent.event_timestamp.asc()).all()

    def get_watched_channels_summary(self, session: Session, user_id: int) -> List[Dict[str, Any]]:
        try:
            active_statuses = [UserTradeStatusEnum.ACTIVATED, UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.WATCHLIST]
            subquery = (
                select(UserTrade.watched_channel_id, func.count(UserTrade.id).label("active_trade_count"))
                .where(UserTrade.user_id == user_id, UserTrade.status.in_(active_statuses))
                .group_by(UserTrade.watched_channel_id).subquery()
            )
            stmt = (
                select(WatchedChannel.id, WatchedChannel.channel_title, WatchedChannel.telegram_channel_id, func.coalesce(subquery.c.active_trade_count, 0).label("active_trade_count"))
                .join(subquery, subquery.c.watched_channel_id == WatchedChannel.id, isouter=True)
                .where(WatchedChannel.user_id == user_id, WatchedChannel.is_active == True)
                .order_by(WatchedChannel.channel_title)
            )
            results = session.execute(stmt).all()
            direct_input_count_stmt = (
                select(func.count(UserTrade.id))
                .where(UserTrade.user_id == user_id, UserTrade.status.in_(active_statuses), UserTrade.watched_channel_id.is_(None))
            )
            direct_input_count = session.execute(direct_input_count_stmt).scalar() or 0
            summary_list = [{"id": r.id, "title": r.channel_title, "count": r.active_trade_count} for r in results]
            if direct_input_count > 0: summary_list.append({"id": "direct", "title": "Direct Input", "count": direct_input_count})
            return summary_list
        except Exception as e:
            logger.error(f"Error fetching watched channels summary: {e}", exc_info=True)
            return []
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/repository.py ---