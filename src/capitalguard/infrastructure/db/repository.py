# --- src/capitalguard/infrastructure/db/repository.py --- V 2.5 (Indentation Hotfix)
"""
Repository layer — provides clean data access abstractions.
✅ Includes ParsingRepository.
✅ Updated RecommendationRepository with Decimal handling and new methods.
✅ HOTFIX: Added missing 'import sqlalchemy as sa' to fix NameError.
✅ HOTFIX (v2.5): Corrected IndentationError in `find_or_create` user method.
"""

import logging
from typing import List, Optional, Any, Dict
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session, joinedload, selectinload
import sqlalchemy as sa # ✅ HOTFIX: Added missing import
from sqlalchemy import and_ # Import 'and_' for combined filters

# Import domain entities and value objects
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType as OrderTypeEntity,
    ExitStrategy as ExitStrategyEntity,
    UserType as UserTypeEntity # Import UserType from domain
)
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side

# Import ORM models
from .models import (
    User, Channel, Recommendation, RecommendationEvent,
    PublishedMessage, UserTrade, UserTradeStatus, RecommendationStatusEnum,
    # ✅ NEW: Import parsing models
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
            # Example: Ensure user_type is updated if provided (e.g., during registration)
            if 'user_type' in kwargs and user.user_type != kwargs['user_type']:
                 user.user_type = kwargs['user_type']
                 updated = True
            # Activate user if specified (e.g., after admin grants access)
            if 'is_active' in kwargs and user.is_active != kwargs['is_active']:
                user.is_active = kwargs['is_active']
                updated = True
            
            # ✅ INDENTATION FIX: This block must be inside the `if user:` block
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
# CHANNEL REPOSITORY
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

# ==========================================================
# PARSING REPOSITORY (✅ NEW)
# ==========================================================
class ParsingRepository:
    """Repository for ParsingTemplate and ParsingAttempt entities."""

    def __init__(self, session: Session):
        self.session = session

    def add_attempt(self, **kwargs) -> ParsingAttempt:
        """Adds a new parsing attempt record and returns the instance."""
        attempt = ParsingAttempt(**kwargs)
        self.session.add(attempt)
        self.session.flush() # Get ID immediately
        logger.debug(f"ParsingAttempt record created with ID: {attempt.id}")
        return attempt

    def update_attempt(self, attempt_id: int, **kwargs):
        """Updates an existing parsing attempt identified by its ID."""
        logger.debug(f"Updating ParsingAttempt ID: {attempt_id} with data: {kwargs}")
        attempt = self.session.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
        if attempt:
            for key, value in kwargs.items():
                setattr(attempt, key, value)
            self.session.flush() # Persist changes within the transaction
            logger.debug(f"ParsingAttempt ID: {attempt_id} updated successfully.")
        else:
            logger.warning(f"Attempted to update non-existent ParsingAttempt ID: {attempt_id}")


    def get_active_templates(self, user_id: Optional[int] = None) -> List[ParsingTemplate]:
        """
        Gets active public templates (is_public=True) and private templates
        owned by the specified user (analyst_id=user_id).
        Orders by confidence score descending (best first).
        """
        query = self.session.query(ParsingTemplate).filter(
            sa.or_( # ✅ HOTFIX: This line now works
                ParsingTemplate.is_public == True,
                ParsingTemplate.analyst_id == user_id
            )
        ).order_by(
            # Prioritize higher confidence score, fallback to ID for deterministic order
            ParsingTemplate.confidence_score.desc().nullslast(),
            ParsingTemplate.id
        )
        templates = query.all()
        logger.debug(f"Fetched {len(templates)} active parsing templates for user_id={user_id}.")
        return templates

    def add_template(self, **kwargs) -> ParsingTemplate:
        """Adds a new parsing template, typically private initially."""
        # Ensure is_public defaults to False if not provided
        kwargs.setdefault('is_public', False)
        template = ParsingTemplate(**kwargs)
        self.session.add(template)
        self.session.flush()
        logger.info(f"ParsingTemplate created with ID: {template.id} for analyst_id={template.analyst_id}")
        return template

    def find_template_by_id(self, template_id: int) -> Optional[ParsingTemplate]:
        """Finds a parsing template by its ID."""
        return self.session.query(ParsingTemplate).filter(ParsingTemplate.id == template_id).first()

# ==========================================================
# RECOMMENDATION REPOSITORY (Updated)
# ==========================================================
class RecommendationRepository:
    """Repository for Recommendation and UserTrade ORM models."""

    @staticmethod
    def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
        """Safely convert any value to a Decimal, returning default on failure."""
        if isinstance(value, Decimal):
             # Handle potential non-finite values from calculations
            return value if value.is_finite() else default
        if value is None:
            return default
        try:
            # Convert string representations, including those from JSON
            d = Decimal(str(value))
            return d if d.is_finite() else default
        except (InvalidOperation, TypeError, ValueError):
            logger.debug(f"Could not convert '{value}' to Decimal, using default '{default}'.")
            return default

    @staticmethod
    def _to_entity(row: Recommendation) -> Optional[RecommendationEntity]:
        """Converts a Recommendation ORM model to its domain entity representation."""
        if not row: return None
        try:
            targets_data = row.targets or []
            # Ensure target prices are Decimals for the domain object
            formatted_targets = [
                {"price": RecommendationRepository._to_decimal(t.get("price")),
                 "close_percent": t.get("close_percent", 0.0)} # Keep as float for domain Target
                for t in targets_data if t.get("price") is not None
            ]
            if not formatted_targets:
                 logger.warning(f"Recommendation {row.id} has no valid targets in JSON data: {row.targets}")
                 # Decide if this should be an error or return None/empty Targets
                 # For now, let Targets raise ValueError if list is empty after processing

            entity = RecommendationEntity(
                id=row.id,
                analyst_id=row.analyst_id,
                asset=Symbol(row.asset),
                side=Side(row.side),
                entry=Price(RecommendationRepository._to_decimal(row.entry)),
                stop_loss=Price(RecommendationRepository._to_decimal(row.stop_loss)),
                # Targets expects list of dicts with Decimal prices
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
                events=list(row.events or []), # Eager load events if configured
                exit_strategy=ExitStrategyEntity(row.exit_strategy.value),
            )
            # Add profit stop fields if they exist on the ORM model
            if hasattr(row, 'profit_stop_active'):
                 setattr(entity, 'profit_stop_active', row.profit_stop_active)
                 setattr(entity, 'profit_stop_mode', row.profit_stop_mode)
                 setattr(entity, 'profit_stop_price', RecommendationRepository._to_decimal(row.profit_stop_price) if row.profit_stop_price is not None else None)
                 setattr(entity, 'profit_stop_trailing_value', RecommendationRepository._to_decimal(row.profit_stop_trailing_value) if row.profit_stop_trailing_value is not None else None)

            return entity
        except Exception as e:
            # Log detailed error including row ID
            logger.error(f"Error translating ORM Recommendation ID {getattr(row, 'id', 'N/A')} to entity: {e}", exc_info=True)
            return None

    def get(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        """Gets a Recommendation ORM object by ID, loading analyst and events."""
        return session.query(Recommendation).options(
            joinedload(Recommendation.analyst),
            selectinload(Recommendation.events) # Use selectinload for events (potentially many)
        ).filter(Recommendation.id == rec_id).first()

    def get_for_update(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        """Gets a Recommendation ORM object by ID with a lock for updating."""
        return session.query(Recommendation).filter(Recommendation.id == rec_id).with_for_update().first()

    def list_all_active_triggers_data(self, session: Session) -> List[Dict[str, Any]]:
        """Gets raw data (as dicts) for all PENDING/ACTIVE recommendations for the AlertService."""
        active_recs = self.get_all_active_recs(session) # Fetch ORM objects
        trigger_data = []
        for rec in active_recs:
            try:
                # Convert necessary fields, ensuring Decimals for prices
                entry_dec = self._to_decimal(rec.entry)
                sl_dec = self._to_decimal(rec.stop_loss)
                targets_list = [
                     {"price": self._to_decimal(t.get("price")),
                      "close_percent": t.get("close_percent", 0.0)}
                     for t in (rec.targets or []) if t.get("price") is not None
                ]

                # Handle potential None analyst relationship
                user_id_str = str(rec.analyst.telegram_user_id) if rec.analyst else None
                if not user_id_str:
                    logger.warning(f"Skipping trigger for Rec ID {rec.id}: Analyst relationship not loaded or user missing.")
                    continue

                data = {
                    "id": rec.id,
                    "user_id": user_id_str, # Use Telegram ID for consistency if needed by AlertService logic
                    "asset": rec.asset,
                    "side": rec.side,
                    "entry": entry_dec,
                    "stop_loss": sl_dec,
                    "targets": targets_list, # Pass list of dicts with Decimals
                    "status": rec.status, # Pass the Enum member
                    "order_type": rec.order_type, # Pass the Enum member
                    "market": rec.market,
                    "is_user_trade": False, # Explicitly mark as not a user trade
                    "processed_events": {e.event_type for e in rec.events},
                    
                    # Add profit stop fields using getattr for safety
                    "profit_stop_mode": getattr(rec, 'profit_stop_mode', 'NONE'),
                    "profit_stop_price": self._to_decimal(getattr(rec, 'profit_stop_price', None)) if getattr(rec, 'profit_stop_price', None) is not None else None,
                    "profit_stop_trailing_value": self._to_decimal(getattr(rec, 'profit_stop_trailing_value', None)) if getattr(rec, 'profit_stop_trailing_value', None) is not None else None,
                    "profit_stop_active": getattr(rec, 'profit_stop_active', False),
                }
                trigger_data.append(data)
            except Exception as e:
                 logger.error(f"Failed to process trigger data for rec #{rec.id}: {e}", exc_info=True)
        logger.debug(f"Generated {len(trigger_data)} trigger data items.")
        return trigger_data

    def get_published_messages(self, session: Session, rec_id: int) -> List[PublishedMessage]:
        """Gets all PublishedMessage ORM records for a recommendation."""
        return session.query(PublishedMessage).filter(PublishedMessage.recommendation_id == rec_id).all()

    def get_open_recs_for_analyst(self, session: Session, analyst_user_id: int) -> List[Recommendation]:
        """Gets all open (PENDING/ACTIVE) Recommendation ORM objects for an analyst."""
        return session.query(Recommendation).filter(
            Recommendation.analyst_id == analyst_user_id,
            Recommendation.is_shadow.is_(False),
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE]),
        ).order_by(Recommendation.created_at.desc()).all()

    def get_open_trades_for_trader(self, session: Session, trader_user_id: int) -> List[UserTrade]:
        """Gets all open UserTrade ORM objects for a trader."""
        return session.query(UserTrade).filter(
            UserTrade.user_id == trader_user_id,
            UserTrade.status == UserTradeStatus.OPEN,
        ).order_by(UserTrade.created_at.desc()).all()

    def get_user_trade_by_id(self, session: Session, trade_id: int) -> Optional[UserTrade]:
        """Gets a specific UserTrade ORM object by its ID."""
        return session.query(UserTrade).filter(UserTrade.id == trade_id).first()

    def find_user_trade_by_source_id(self, session: Session, user_id: int, rec_id: int) -> Optional[UserTrade]:
        """Finds an open UserTrade linked to a specific user and recommendation."""
        return session.query(UserTrade).filter(
            UserTrade.user_id == user_id,
            UserTrade.source_recommendation_id == rec_id,
            UserTrade.status == UserTradeStatus.OPEN
        ).first()

    def get_events_for_recommendation(self, session: Session, rec_id: int) -> List[RecommendationEvent]:
        """Gets all RecommendationEvent ORM objects for a recommendation, ordered by time."""
        return session.query(RecommendationEvent).filter(
            RecommendationEvent.recommendation_id == rec_id
        ).order_by(RecommendationEvent.event_timestamp.asc()).all()

    def get_all_active_recs(self, session: Session) -> List[Recommendation]:
        """Fetches all PENDING or ACTIVE Recommendation ORM objects with related data eager-loaded."""
        return session.query(Recommendation).options(
            selectinload(Recommendation.events), # Eager load events
            joinedload(Recommendation.analyst) # Eager load analyst info
        ).filter(
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE])
        ).all()

    def get_active_recs_for_asset_and_market(self, session: Session, asset: str, market: str) -> List[Recommendation]:
        """Fetches active recommendations for a specific asset and market."""
        # Ensure asset is uppercase for consistent querying
        asset_upper = asset.strip().upper()
        return session.query(Recommendation).filter(
            and_( # Use and_() for combining multiple conditions
                Recommendation.asset == asset_upper,
                Recommendation.market == market, # Assuming market is stored consistently
                Recommendation.status == RecommendationStatusEnum.ACTIVE
            )
        ).all()

# --- END of repository update ---