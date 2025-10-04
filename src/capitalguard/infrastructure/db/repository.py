# src/capitalguard/infrastructure/db/repository.py (v3.0 - Multi-Tenant Compatible)
import logging
from datetime import datetime, timezone
from typing import List, Optional, Any, Union, Dict
from types import SimpleNamespace

from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload, selectinload

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import (
    User, UserType, Channel, Recommendation, RecommendationEvent, 
    PublishedMessage, UserTrade, UserTradeStatus
)

log = logging.getLogger(__name__)

class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        user = self.find_by_telegram_id(telegram_id)
        if user:
            return user
        
        log.info("Creating new user for telegram_id=%s", telegram_id)
        new_user = User(
            telegram_user_id=telegram_id,
            first_name=kwargs.get("first_name"),
            username=kwargs.get("username"),
            is_active=False, # New users are inactive by default
        )
        self.session.add(new_user)
        self.session.commit()
        self.session.refresh(new_user)
        return new_user

class ChannelRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_by_analyst(self, analyst_user_id: int, only_active: bool = False) -> List[Channel]:
        q = self.session.query(Channel).filter(Channel.analyst_id == analyst_user_id)
        if only_active:
            q = q.filter(Channel.is_active.is_(True))
        return q.order_by(Channel.created_at.desc()).all()

class RecommendationRepository:
    @staticmethod
    def _to_entity(row: Recommendation) -> Optional[Recommendation]:
        # The ORM model is now the source of truth, we can adapt it to the entity if needed
        # For now, we'll simplify and assume the ORM object is sufficient
        return row

    def get(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        return session.query(Recommendation).options(
            joinedload(Recommendation.analyst), selectinload(Recommendation.events)
        ).filter(Recommendation.id == rec_id).first()

    def get_for_update(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        return session.query(Recommendation).options(selectinload(Recommendation.events)).filter(Recommendation.id == rec_id).with_for_update().first()

    def get_events_for_recommendation(self, session: Session, rec_id: int) -> List[RecommendationEvent]:
        return session.query(RecommendationEvent).filter(
            RecommendationEvent.recommendation_id == rec_id
        ).order_by(RecommendationEvent.event_timestamp.asc()).all()

    def list_all_active_triggers_data(self, session: Session) -> List[Dict[str, Any]]:
        # This now needs to fetch from UserTrades, not Recommendations
        results = session.query(UserTrade).options(
            joinedload(UserTrade.user)
        ).filter(
            UserTrade.status == UserTradeStatus.OPEN
        ).all()

        # We also need to fetch PENDING official recommendations for analysts
        pending_recs = session.query(Recommendation).options(
            joinedload(Recommendation.analyst)
        ).filter(
            Recommendation.status == RecommendationStatusEnum.PENDING
        ).all()

        trigger_data = []
        # Add triggers for user trades (SL and TPs)
        for trade in results:
            trigger_data.append({
                "id": trade.id,
                "user_id": str(trade.user.telegram_user_id),
                "asset": trade.asset,
                "side": trade.side,
                "entry": float(trade.entry),
                "stop_loss": float(trade.stop_loss),
                "targets": trade.targets,
                "status": RecommendationStatus.ACTIVE, # User trades are conceptually "ACTIVE"
                "is_user_trade": True,
                "processed_events": set() # User trades don't have events yet
            })

        # Add triggers for analyst's pending recommendations (ENTRY only)
        for rec in pending_recs:
            trigger_data.append({
                "id": rec.id,
                "user_id": str(rec.analyst.telegram_user_id),
                "asset": rec.asset,
                "side": rec.side,
                "entry": float(rec.entry),
                "stop_loss": float(rec.stop_loss),
                "targets": rec.targets,
                "status": RecommendationStatus.PENDING,
                "is_user_trade": False,
                "processed_events": {e.event_type for e in rec.events}
            })
        return trigger_data