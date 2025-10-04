# - src/capitalguard/infrastructure/db/repository.py (v3.1 - Import Hotfix)
import logging
from typing import List, Optional, Any, Union, Dict
from types import SimpleNamespace

from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload, selectinload

# ✅ IMPORT FIX: Import Enums from their new correct location
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType, 
    ExitStrategy
)
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import (
    User, UserType, Channel, Recommendation, RecommendationEvent, 
    PublishedMessage, UserTrade, UserTradeStatus, RecommendationStatusEnum, AnalystProfile
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
            is_active=False,
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
    def _to_entity(row: Recommendation) -> Optional[RecommendationEntity]:
        if not row: return None
        
        return RecommendationEntity(
            id=row.id,
            asset=Symbol(row.asset),
            side=Side(row.side),
            entry=Price(float(row.entry)),
            stop_loss=Price(float(row.stop_loss)),
            targets=Targets(row.targets),
            order_type=OrderType(row.order_type.value),
            status=RecommendationStatusEntity[row.status.name],
            market=row.market,
            notes=row.notes,
            user_id=str(row.analyst.telegram_user_id) if row.analyst else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
            exit_price=float(row.exit_price) if row.exit_price is not None else None,
            activated_at=row.activated_at,
            closed_at=row.closed_at,
            exit_strategy=ExitStrategy(row.exit_strategy.value),
            open_size_percent=float(row.open_size_percent) if row.open_size_percent is not None else 100.0,
            events=[SimpleNamespace(
                event_type=ev.event_type,
                event_data=ev.event_data,
                event_timestamp=ev.event_timestamp
            ) for ev in row.events]
        )

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
        active_user_trades = session.query(UserTrade).options(
            joinedload(UserTrade.user)
        ).filter(
            UserTrade.status == UserTradeStatus.OPEN
        ).all()

        pending_recommendations = session.query(Recommendation).options(
            joinedload(Recommendation.analyst), selectinload(Recommendation.events)
        ).filter(
            Recommendation.status == RecommendationStatusEnum.PENDING
        ).all()

        trigger_data = []
        for trade in active_user_trades:
            trigger_data.append({
                "id": trade.id,
                "user_id": str(trade.user.telegram_user_id),
                "asset": trade.asset,
                "side": trade.side,
                "entry": float(trade.entry),
                "stop_loss": float(trade.stop_loss),
                "targets": trade.targets,
                "status": RecommendationStatusEnum.ACTIVE,
                "is_user_trade": True,
                "processed_events": set() 
            })

        for rec in pending_recommendations:
            trigger_data.append({
                "id": rec.id,
                "user_id": str(rec.analyst.telegram_user_id),
                "asset": rec.asset,
                "side": rec.side,
                "entry": float(rec.entry),
                "stop_loss": float(rec.stop_loss),
                "targets": rec.targets,
                "status": RecommendationStatusEnum.PENDING,
                "is_user_trade": False,
                "processed_events": {e.event_type for e in rec.events}
            })
        return trigger_data
        
    def get_published_messages(self, session: Session, rec_id: int) -> List[PublishedMessage]:
        return session.query(PublishedMessage).filter(PublishedMessage.recommendation_id == rec_id).all()

    def get_open_recs_for_analyst(self, session: Session, analyst_user_id: int) -> List[Recommendation]:
        return session.query(Recommendation).filter(
            Recommendation.analyst_id == analyst_user_id,
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE])
        ).order_by(Recommendation.created_at.desc()).all()

    def get_open_trades_for_trader(self, session: Session, trader_user_id: int) -> List[UserTrade]:
        return session.query(UserTrade).filter(
            UserTrade.user_id == trader_user_id,
            UserTrade.status == UserTradeStatus.OPEN
        ).order_by(UserTrade.created_at.desc()).all()