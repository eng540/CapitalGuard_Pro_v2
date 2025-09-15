import logging
from datetime import datetime, timezone
from typing import List, Optional, Any, Union, Dict

from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User, Channel, PublishedMessage, RecommendationEvent
from .base import SessionLocal

log = logging.getLogger(__name__)

class _SessionManager:
    def __init__(self, session: Optional[Session] = None):
        self._provided_session = session
        self._session = None

    def __enter__(self) -> Session:
        if self._provided_session:
            self._session = self._provided_session
        else:
            self._session = SessionLocal()
        return self._session

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._provided_session and self._session:
            if exc_type:
                self._session.rollback()
            else:
                self._session.commit()
            self._session.close()

class UserRepository:
    def find_by_telegram_id(self, telegram_id: int, session: Optional[Session] = None) -> Optional[User]:
        with _SessionManager(session) as s:
            return s.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_or_create(self, telegram_id: int, session: Optional[Session] = None, **kwargs) -> User:
        with _SessionManager(session) as s:
            user = s.query(User).filter(User.telegram_user_id == telegram_id).first()
            if user:
                return user
            
            log.info("Creating new user for telegram_id=%s", telegram_id)
            new_user = User(
                telegram_user_id=telegram_id,
                email=kwargs.get("email") or f"tg{telegram_id}@telegram.local",
                user_type=(kwargs.get("user_type") or "trader"),
                is_active=True,
                first_name=kwargs.get("first_name"),
            )
            s.add(new_user)
            s.flush()
            s.refresh(new_user)
            return new_user

class RecommendationRepository:
    @staticmethod
    def _to_entity(row: RecommendationORM) -> Optional[Recommendation]:
        if not row: return None
        user_telegram_id = str(row.user.telegram_user_id) if getattr(row, "user", None) else None
        return Recommendation(
            id=row.id, asset=Symbol(row.asset), side=Side(row.side), entry=Price(row.entry),
            stop_loss=Price(row.stop_loss), targets=Targets(list(row.targets or [])),
            order_type=OrderType(row.order_type), status=RecommendationStatus(row.status),
            market=row.market, notes=row.notes, user_id=user_telegram_id, created_at=row.created_at,
            updated_at=row.updated_at, exit_price=row.exit_price, activated_at=row.activated_at,
            closed_at=row.closed_at, alert_meta=dict(row.alert_meta or {}),
            highest_price_reached=row.highest_price_reached, lowest_price_reached=row.lowest_price_reached,
            exit_strategy=ExitStrategy(row.exit_strategy), profit_stop_price=row.profit_stop_price,
            open_size_percent=row.open_size_percent, events=row.events,
        )

    def add_with_event(self, rec: Recommendation, session: Optional[Session] = None) -> Recommendation:
        with _SessionManager(session) as s:
            user = UserRepository().find_or_create(int(rec.user_id), session=s)
            targets_for_db = [v.__dict__ for v in rec.targets.values]
            row = RecommendationORM(
                user_id=user.id, asset=rec.asset.value, side=rec.side.value, entry=rec.entry.value,
                stop_loss=rec.stop_loss.value, targets=targets_for_db, order_type=rec.order_type,
                status=rec.status, market=rec.market, notes=rec.notes, activated_at=rec.activated_at,
                exit_strategy=rec.exit_strategy, profit_stop_price=rec.profit_stop_price,
                open_size_percent=rec.open_size_percent,
            )
            s.add(row)
            s.flush()
            create_event = RecommendationEvent(
                recommendation_id=row.id, event_type='CREATE',
                event_timestamp=row.created_at, event_data={'entry': rec.entry.value, 'sl': rec.stop_loss.value}
            )
            s.add(create_event)
            s.flush()
            s.refresh(row, attribute_names=["user"])
            return self._to_entity(row)

    def update_with_event(self, rec: Recommendation, event_type: str, event_data: Dict[str, Any], session: Optional[Session] = None) -> Recommendation:
        with _SessionManager(session) as s:
            row = s.query(RecommendationORM).filter(RecommendationORM.id == rec.id).first()
            if not row: raise ValueError(f"Recommendation #{rec.id} not found")
            
            row.status = rec.status
            row.stop_loss = rec.stop_loss.value
            row.targets = [v.__dict__ for v in rec.targets.values]
            row.exit_price = rec.exit_price
            row.activated_at = rec.activated_at
            row.closed_at = rec.closed_at
            row.alert_meta = rec.alert_meta
            row.highest_price_reached = rec.highest_price_reached
            row.lowest_price_reached = rec.lowest_price_reached
            row.exit_strategy = rec.exit_strategy
            row.profit_stop_price = rec.profit_stop_price
            row.open_size_percent = rec.open_size_percent

            new_event = RecommendationEvent(recommendation_id=row.id, event_type=event_type, event_data=event_data)
            s.add(new_event)
            s.flush()
            s.refresh(row, attribute_names=["user"])
            return self._to_entity(row)

    def update(self, rec: Recommendation, session: Optional[Session] = None) -> Recommendation:
        with _SessionManager(session) as s:
            row = s.query(RecommendationORM).filter(RecommendationORM.id == rec.id).first()
            if not row: raise ValueError(f"Recommendation #{rec.id} not found")
            
            row.highest_price_reached = rec.highest_price_reached
            row.lowest_price_reached = rec.lowest_price_reached
            
            s.flush()
            s.refresh(row, attribute_names=["user"])
            return self._to_entity(row)

    def get(self, rec_id: int, session: Optional[Session] = None) -> Optional[Recommendation]:
        with _SessionManager(session) as s:
            row = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(RecommendationORM.id == rec_id).first()
            return self._to_entity(row)

    def get_by_id_for_user(self, rec_id: int, user_telegram_id: Union[int, str], session: Optional[Session] = None) -> Optional[Recommendation]:
        with _SessionManager(session) as s:
            user = UserRepository().find_by_telegram_id(int(user_telegram_id), session=s)
            if not user: return None
            row = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(RecommendationORM.id == rec_id, RecommendationORM.user_id == user.id).first()
            return self._to_entity(row)

    def list_open(self, session: Optional[Session] = None) -> List[Recommendation]:
        with _SessionManager(session) as s:
            rows = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(RecommendationORM.status.in_([RecommendationStatus.PENDING, RecommendationStatus.ACTIVE])).order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def get_events_for_recommendations(self, rec_ids: List[int], session: Optional[Session] = None) -> Dict[int, set[str]]:
        if not rec_ids: return {}
        with _SessionManager(session) as s:
            results = s.query(RecommendationEvent.recommendation_id, RecommendationEvent.event_type).filter(RecommendationEvent.recommendation_id.in_(rec_ids)).all()
            event_map = {}
            for rec_id, event_type in results:
                event_map.setdefault(rec_id, set()).add(event_type)
            return event_map

    def get_published_messages(self, rec_id: int, session: Optional[Session] = None) -> List[PublishedMessage]:
        with _SessionManager(session) as s:
            return s.query(PublishedMessage).filter(PublishedMessage.recommendation_id == rec_id).all()

    def list_open_for_user(self, user_telegram_id: Union[int, str], **filters) -> List[Recommendation]:
        with SessionLocal() as s:
            user = UserRepository().find_by_telegram_id(int(user_telegram_id), session=s)
            if not user: return []
            q = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(RecommendationORM.user_id == user.id, RecommendationORM.status.in_([RecommendationStatus.PENDING, RecommendationStatus.ACTIVE]))
            if filters.get("symbol"): q = q.filter(RecommendationORM.asset.ilike(f'%{filters["symbol"].upper()}%'))
            if filters.get("side"): q = q.filter(RecommendationORM.side == Side(filters["side"].upper()).value)
            if filters.get("status"): q = q.filter(RecommendationORM.status == RecommendationStatus(filters["status"].upper()))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]