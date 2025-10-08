# src/capitalguard/infrastructure/db/repository.py (v25.0 - FINAL & UNIFIED)
"""
The Repository layer, implementing the data access ports defined in the domain.
This is the ONLY place where the application interacts with SQLAlchemy ORM models.
It is responsible for translating between ORM models and domain entities.
"""

import logging
from typing import List, Optional, Any, Dict
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload, selectinload

from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType as OrderTypeEntity,
    ExitStrategy as ExitStrategyEntity,
    UserTrade as UserTradeEntity,
    UserTradeStatus as UserTradeStatusEntity,
    UserType as UserTypeEntity
)
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import (
    User, UserType, Channel, Recommendation, RecommendationEvent,
    PublishedMessage, UserTrade, UserTradeStatus, RecommendationStatusEnum
)

logger = logging.getLogger(__name__)

class UserRepository:
    """Repository for User entities."""
    
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        user = self.find_by_telegram_id(telegram_id)
        if user:
            # Update user details if they have changed
            if kwargs.get("first_name") and user.first_name != kwargs["first_name"]:
                user.first_name = kwargs["first_name"]
            if kwargs.get("username") and user.username != kwargs["username"]:
                user.username = kwargs["username"]
            return user
        
        logger.info("Creating new user for telegram_id=%s", telegram_id)
        new_user = User(
            telegram_user_id=telegram_id,
            first_name=kwargs.get("first_name"),
            username=kwargs.get("username"),
            is_active=False, # New users are inactive by default
            user_type=kwargs.get("user_type", UserType.TRADER)
        )
        self.session.add(new_user)
        self.session.flush()
        return new_user

class ChannelRepository:
    """Repository for Channel entities."""
    
    def __init__(self, session: Session):
        self.session = session

    def list_by_analyst(self, analyst_user_id: int, only_active: bool = False) -> List[Channel]:
        query = self.session.query(Channel).filter(Channel.analyst_id == analyst_user_id)
        if only_active:
            query = query.filter(Channel.is_active.is_(True))
        return query.order_by(Channel.created_at.desc()).all()

class RecommendationRepository:
    """Repository for Recommendation and UserTrade entities."""
    
    @staticmethod
    def _to_entity(row: Recommendation) -> Optional[RecommendationEntity]:
        """Translates the Recommendation ORM model to a domain entity."""
        if not row: 
            return None
        
        try:
            # Ensure targets are correctly formatted
            targets_data = row.targets or []
            formatted_targets = [
                {'price': Decimal(t['price']), 'close_percent': t.get('close_percent', 0)}
                for t in targets_data
            ]

            return RecommendationEntity(
                id=row.id,
                analyst_id=row.analyst_id,
                asset=Symbol(row.asset),
                side=Side(row.side),
                entry=Price(row.entry),
                stop_loss=Price(row.stop_loss),
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
                exit_strategy=ExitStrategyEntity(row.exit_strategy.value),
                open_size_percent=float(row.open_size_percent),
                is_shadow=row.is_shadow,
                events=list(row.events) # Pass ORM events directly
            )
        except Exception as e:
            logger.error("Error translating Recommendation ORM to entity for ID %s: %s", row.id, e, exc_info=True)
            return None

    def get(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        """Gets a single Recommendation ORM model by its ID."""
        return session.query(Recommendation).options(
            joinedload(Recommendation.analyst), 
            selectinload(Recommendation.events)
        ).filter(Recommendation.id == rec_id).first()

    def get_for_update(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        """Gets a single Recommendation ORM model with a row-level lock for updating."""
        return session.query(Recommendation).filter(Recommendation.id == rec_id).with_for_update().first()

    def list_all_active_triggers_data(self, session: Session) -> List[Dict[str, Any]]:
        """
        Fetches all data required for price monitoring from both active recommendations
        and open user trades (via shadow recommendations). This is the single source
        of truth for the AlertService index.
        """
        # We only need to query Recommendations, as UserTrades are tracked via shadow recs.
        active_recs = session.query(Recommendation).options(
            selectinload(Recommendation.events)
        ).filter(
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE])
        ).all()

        trigger_data = []
        for rec in active_recs:
            # Determine if this is a shadow rec for a user trade
            is_user_trade = rec.is_shadow
            user_id_for_trigger = None
            if is_user_trade and rec.user_trades:
                # For a shadow rec, the relevant user is the trader, not the system analyst
                user_id_for_trigger = rec.user_trades[0].user.telegram_user_id
            elif not is_user_trade:
                user_id_for_trigger = rec.analyst.telegram_user_id

            if user_id_for_trigger is None:
                logger.warning("Could not determine user for trigger from recommendation #%s. Skipping.", rec.id)
                continue

            trigger_data.append({
                "id": rec.id,
                "user_id": str(user_id_for_trigger),
                "asset": rec.asset,
                "side": rec.side,
                "entry": rec.entry,
                "stop_loss": rec.stop_loss,
                "targets": rec.targets,
                "status": rec.status,
                "order_type": rec.order_type,
                "market": rec.market,
                "is_user_trade": is_user_trade,
                "processed_events": {e.event_type for e in rec.events},
            })
        return trigger_data

    def get_published_messages(self, session: Session, rec_id: int) -> List[PublishedMessage]:
        """Gets all publication records for a given recommendation ID."""
        return session.query(PublishedMessage).filter(
            PublishedMessage.recommendation_id == rec_id
        ).all()

    def get_open_recs_for_analyst(self, session: Session, analyst_user_id: int) -> List[Recommendation]:
        """Gets all open (PENDING or ACTIVE) recommendations for a specific analyst."""
        return session.query(Recommendation).filter(
            Recommendation.analyst_id == analyst_user_id,
            Recommendation.is_shadow.is_(False),
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE])
        ).order_by(Recommendation.created_at.desc()).all()

    def get_open_trades_for_trader(self, session: Session, trader_user_id: int) -> List[UserTrade]:
        """Gets all open personal trades for a specific trader."""
        return session.query(UserTrade).filter(
            UserTrade.user_id == trader_user_id,
            UserTrade.status == UserTradeStatus.OPEN
        ).order_by(UserTrade.created_at.desc()).all()

    def get_user_trade_by_id(self, session: Session, trade_id: int) -> Optional[UserTrade]:
        """Gets a single UserTrade by its ID."""
        return session.query(UserTrade).filter(UserTrade.id == trade_id).first()