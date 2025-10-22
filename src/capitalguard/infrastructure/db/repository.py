# src/capitalguard/infrastructure/db/repository.py (v2.0 - AlertService Integration)
"""
Repository layer — provides clean data access abstractions.
✅ NEW: Added `get_all_active_recs` and `get_active_recs_for_asset_and_market`
to efficiently support the AlertService and StrategyEngine.
"""

import logging
from typing import List, Optional, Any, Dict
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import and_

from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType as OrderTypeEntity,
    ExitStrategy as ExitStrategyEntity,
)
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import (
    User, UserType, Channel, Recommendation, RecommendationEvent,
    PublishedMessage, UserTrade, UserTradeStatus, RecommendationStatusEnum
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
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_by_id(self, user_id: int) -> Optional[User]:
        return self.session.query(User).filter(User.id == user_id).first()

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        user = self.find_by_telegram_id(telegram_id)
        if user:
            # Update user info if changed
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
            is_active=False, # Users are inactive by default
            user_type=kwargs.get("user_type", UserType.TRADER),
        )
        self.session.add(new_user)
        self.session.flush()
        return new_user

# ==========================================================
# CHANNEL REPOSITORY
# ==========================================================
class ChannelRepository:
    """Repository for Channel entities."""
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id_and_analyst(self, channel_id: int, analyst_id: int) -> Optional[Channel]:
        return self.session.query(Channel).filter(Channel.telegram_channel_id == channel_id, Channel.analyst_id == analyst_id).one_or_none()

    def list_by_analyst(self, analyst_id: int, only_active: bool = True) -> List[Channel]:
        query = self.session.query(Channel).filter(Channel.analyst_id == analyst_id)
        if only_active:
            query = query.filter(Channel.is_active == True)
        return query.order_by(Channel.created_at.desc()).all()

    def add(self, analyst_id: int, telegram_channel_id: int, username: Optional[str], title: Optional[str]) -> Channel:
        new_channel = Channel(analyst_id=analyst_id, telegram_channel_id=telegram_channel_id, username=username, title=title, is_active=True)
        self.session.add(new_channel)
        self.session.flush()
        return new_channel

    def delete(self, channel: Channel):
        self.session.delete(channel)
        self.session.flush()

# ==========================================================
# RECOMMENDATION REPOSITORY
# ==========================================================
class RecommendationRepository:
    """Repository for Recommendation and UserTrade entities."""

    @staticmethod
    def _to_entity(row: Recommendation) -> Optional[RecommendationEntity]:
        if not row: return None
        try:
            targets_data = row.targets or []
            formatted_targets = [{"price": Decimal(t["price"]), "close_percent": t.get("close_percent", 0)} for t in targets_data]
            
            # Create the entity with all available fields
            entity = RecommendationEntity(
                id=row.id,
                analyst_id=row.analyst_id,
                asset=Symbol(row.asset),
                side=Side(row.side),
                entry=Price(Decimal(str(row.entry))),
                stop_loss=Price(Decimal(str(row.stop_loss))),
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
            )
            # Add profit stop fields if they exist
            if hasattr(row, 'profit_stop_active'):
                setattr(entity, 'profit_stop_active', row.profit_stop_active)
                setattr(entity, 'profit_stop_mode', row.profit_stop_mode)
                setattr(entity, 'profit_stop_price', row.profit_stop_price)
                setattr(entity, 'profit_stop_trailing_value', row.profit_stop_trailing_value)
            
            return entity
        except Exception as e:
            logger.error("Error translating ORM to entity for ID %s: %s", row.id, e, exc_info=True)
            return None

    def get(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        return session.query(Recommendation).options(joinedload(Recommendation.analyst), selectinload(Recommendation.events)).filter(Recommendation.id == rec_id).first()

    def get_for_update(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        return session.query(Recommendation).filter(Recommendation.id == rec_id).with_for_update().first()

    def list_all_active_triggers_data(self, session: Session) -> List[Dict[str, Any]]:
        active_recs = self.get_all_active_recs(session)
        trigger_data = []
        for rec in active_recs:
            trigger_data.append({
                "id": rec.id,
                "user_id": str(rec.analyst.telegram_user_id) if rec.analyst else None,
                "asset": rec.asset,
                "side": rec.side,
                "entry": Decimal(str(rec.entry)),
                "stop_loss": Decimal(str(rec.stop_loss)),
                "targets": rec.targets,
                "status": rec.status,
                "processed_events": {e.event_type for e in rec.events},
            })
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
        return session.query(UserTrade).filter(
            UserTrade.user_id == trader_user_id,
            UserTrade.status == UserTradeStatus.OPEN,
        ).order_by(UserTrade.created_at.desc()).all()

    def get_user_trade_by_id(self, session: Session, trade_id: int) -> Optional[UserTrade]:
        return session.query(UserTrade).filter(UserTrade.id == trade_id).first()
    
    def get_events_for_recommendation(self, session: Session, rec_id: int) -> List[RecommendationEvent]:
        return session.query(RecommendationEvent).filter(RecommendationEvent.recommendation_id == rec_id).order_by(RecommendationEvent.event_timestamp.asc()).all()

    # ✅ NEW METHOD
    def get_all_active_recs(self, session: Session) -> List[Recommendation]:
        """Fetches all PENDING or ACTIVE recommendations with related data."""
        return session.query(Recommendation).options(
            selectinload(Recommendation.events),
            joinedload(Recommendation.analyst)
        ).filter(
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE])
        ).all()

    # ✅ NEW METHOD
    def get_active_recs_for_asset_and_market(self, session: Session, asset: str, market: str) -> List[Recommendation]:
        """Fetches active recommendations for a specific asset and market."""
        return session.query(Recommendation).filter(
            and_(
                Recommendation.asset == asset,
                Recommendation.market == market,
                Recommendation.status == RecommendationStatusEnum.ACTIVE
            )
        ).all()