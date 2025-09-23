# --- START OF FINAL, COMPLETE, AND CONCURRENCY-SAFE FILE (Version 13.3.0) ---
# src/capitalguard/infrastructure/db/repository.py

import logging
from datetime import datetime, timezone
from typing import List, Optional, Any, Union, Dict, Set

from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User, Channel, PublishedMessage, RecommendationEvent

log = logging.getLogger(__name__)

class UserRepository:
    """Handles all database operations related to User."""
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        """
        Finds a user by their Telegram ID, or creates a new one if not found.
        This method is now concurrency-safe to prevent race conditions.
        """
        user = self.find_by_telegram_id(telegram_id)
        if user:
            return user
        
        try:
            log.info("Attempting to create new user for telegram_id=%s", telegram_id)
            new_user = User(
                telegram_user_id=telegram_id,
                email=kwargs.get("email") or f"tg{telegram_id}@telegram.local",
                is_active=False,
                first_name=kwargs.get("first_name"),
            )
            self.session.add(new_user)
            self.session.flush()
            self.session.refresh(new_user)
            return new_user
        except IntegrityError:
            log.warning(f"Race condition detected for telegram_id={telegram_id}. Rolling back and fetching existing user.")
            self.session.rollback()
            return self.find_by_telegram_id(telegram_id)

class ChannelRepository:
    """Handles all database operations related to Channel."""
    def __init__(self, session: Session):
        self.session = session

    def list_by_user(self, user_id: int, only_active: bool = False) -> List[Channel]:
        q = self.session.query(Channel).filter(Channel.user_id == user_id)
        if only_active:
            q = q.filter(Channel.is_active.is_(True))
        return q.order_by(Channel.created_at.desc()).all()

    def add(self, owner_user_id: int, telegram_channel_id: int, **kwargs) -> Channel:
        existing_channel = self.session.query(Channel).filter(
            Channel.telegram_channel_id == telegram_channel_id
        ).first()

        if existing_channel:
            log.info(f"Channel {telegram_channel_id} already exists. Updating details.")
            existing_channel.title = kwargs.get("title")
            existing_channel.username = kwargs.get("username")
            existing_channel.last_verified_at = datetime.now(timezone.utc)
            self.session.flush()
            self.session.refresh(existing_channel)
            return existing_channel
        else:
            log.info(f"Adding new channel {telegram_channel_id} for user {owner_user_id}.")
            new_channel = Channel(
                user_id=owner_user_id,
                telegram_channel_id=telegram_channel_id,
                title=kwargs.get("title"),
                username=kwargs.get("username"),
                is_active=True,
                last_verified_at=datetime.now(timezone.utc)
            )
            self.session.add(new_channel)
            self.session.flush()
            self.session.refresh(new_channel)
            return new_channel

    def set_active(self, owner_user_id: int, telegram_channel_id: int, is_active: bool):
        channel = self.session.query(Channel).filter(
            Channel.user_id == owner_user_id,
            Channel.telegram_channel_id == telegram_channel_id
        ).first()
        if channel:
            channel.is_active = is_active
            self.session.flush()

class RecommendationRepository:
    """Handles all database operations related to Recommendation."""
    @staticmethod
    def _to_entity(row: RecommendationORM) -> Optional[Recommendation]:
        if not row: return None
        user_telegram_id = str(row.user.telegram_user_id) if getattr(row, "user", None) else None
        
        targets_data = row.targets or []
        if targets_data and isinstance(targets_data[0], (int, float)):
            targets_vo = Targets([{"price": p, "close_percent": 0} for p in targets_data])
        else:
            targets_vo = Targets(list(targets_data))

        return Recommendation(
            id=row.id, asset=Symbol(row.asset), side=Side(row.side), entry=Price(row.entry),
            stop_loss=Price(row.stop_loss), targets=targets_vo,
            order_type=OrderType(row.order_type), status=RecommendationStatus(row.status),
            market=row.market, notes=row.notes, user_id=user_telegram_id, created_at=row.created_at,
            updated_at=row.updated_at, exit_price=row.exit_price, activated_at=row.activated_at,
            closed_at=row.closed_at, alert_meta=dict(row.alert_meta or {}),
            highest_price_reached=row.highest_price_reached, lowest_price_reached=row.lowest_price_reached,
            exit_strategy=ExitStrategy(row.exit_strategy), profit_stop_price=row.profit_stop_price,
            open_size_percent=row.open_size_percent, events=getattr(row, 'events', None),
        )

    def get_for_update(self, session: Session, rec_id: int) -> Optional[RecommendationORM]:
        return session.query(RecommendationORM).filter(RecommendationORM.id == rec_id).with_for_update().first()

    def update_with_event(self, session: Session, rec: Recommendation, event_type: str, event_data: Dict[str, Any]) -> Recommendation:
        row = self.get_for_update(session, rec.id)
        if not row: raise ValueError(f"Recommendation #{rec.id} not found for update.")
        
        row.status = rec.status.value
        row.stop_loss = rec.stop_loss.value
        row.targets = [v.__dict__ for v in rec.targets.values]
        row.exit_price = rec.exit_price
        row.activated_at = rec.activated_at
        row.closed_at = rec.closed_at
        row.alert_meta = rec.alert_meta
        row.highest_price_reached = rec.highest_price_reached
        row.lowest_price_reached = rec.lowest_price_reached
        row.exit_strategy = rec.exit_strategy.value
        row.profit_stop_price = rec.profit_stop_price
        row.open_size_percent = rec.open_size_percent
        row.notes = rec.notes
        row.market = rec.market

        new_event = RecommendationEvent(recommendation_id=row.id, event_type=event_type, event_data=event_data)
        session.add(new_event)
        session.flush()
        session.refresh(row, attribute_names=["user", "events"])
        return self._to_entity(row)

    def add_with_event(self, session: Session, rec: Recommendation) -> Recommendation:
        user = UserRepository(session).find_by_telegram_id(int(rec.user_id))
        if not user: raise ValueError(f"User with telegram_id {rec.user_id} not found.")

        row = RecommendationORM(
            user_id=user.id, asset=rec.asset.value, side=rec.side.value, entry=rec.entry.value,
            stop_loss=rec.stop_loss.value, targets=[v.__dict__ for v in rec.targets.values],
            order_type=rec.order_type.value, status=rec.status.value, market=rec.market, notes=rec.notes,
            exit_strategy=rec.exit_strategy.value, open_size_percent=rec.open_size_percent,
            activated_at=rec.activated_at, highest_price_reached=rec.highest_price_reached,
            lowest_price_reached=rec.lowest_price_reached
        )
        session.add(row)
        session.flush()
        
        event_type = "CREATED_ACTIVE" if row.status == RecommendationStatus.ACTIVE.value else "CREATED_PENDING"
        new_event = RecommendationEvent(recommendation_id=row.id, event_type=event_type, event_data={})
        session.add(new_event)
        session.flush()
        
        session.refresh(row, attribute_names=["user", "events"])
        return self._to_entity(row)

    def get(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        row = session.query(RecommendationORM).options(
            joinedload(RecommendationORM.user), selectinload(RecommendationORM.events)
        ).filter(RecommendationORM.id == rec_id).first()
        return self._to_entity(row)

    def get_by_id_for_user(self, session: Session, rec_id: int, user_telegram_id: Union[int, str]) -> Optional[Recommendation]:
        user = UserRepository(session).find_by_telegram_id(int(user_telegram_id))
        if not user: return None
        row = session.query(RecommendationORM).options(
            joinedload(RecommendationORM.user), selectinload(RecommendationORM.events)
        ).filter(RecommendationORM.id == rec_id, RecommendationORM.user_id == user.id).first()
        return self._to_entity(row)
        
    # ✅ MODIFIED: Added 'options' parameter to accept query enhancements like joinedload.
    def list_open(self, session: Session, options: Optional[List] = None) -> List[Recommendation]:
        q = session.query(RecommendationORM).options(
            joinedload(RecommendationORM.user)
        ).filter(RecommendationORM.status.in_([RecommendationStatus.PENDING.value, RecommendationStatus.ACTIVE.value]))
        
        if options:
            q = q.options(*options)
            
        rows = q.order_by(RecommendationORM.created_at.desc()).all()
        return [self._to_entity(r) for r in rows]

    def list_open_for_user(self, session: Session, user_telegram_id: int, **filters) -> List[Recommendation]:
        user = UserRepository(session).find_by_telegram_id(user_telegram_id)
        if not user: return []
        q = session.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(
            RecommendationORM.user_id == user.id,
            RecommendationORM.status.in_([RecommendationStatus.PENDING.value, RecommendationStatus.ACTIVE.value])
        )
        if "side" in filters: q = q.filter(RecommendationORM.side == filters["side"].upper())
        if "status" in filters: q = q.filter(RecommendationORM.status == RecommendationStatus(filters["status"].upper()).value)
        if "symbol" in filters: q = q.filter(RecommendationORM.asset == filters["symbol"].upper())
        
        return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    # ✅ MODIFIED: Added 'options' parameter to accept query enhancements like joinedload.
    def list_open_by_symbol(self, session: Session, symbol: str, options: Optional[List] = None) -> List[Recommendation]:
        q = session.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(
            RecommendationORM.asset == symbol.upper(),
            RecommendationORM.status.in_([RecommendationStatus.PENDING.value, RecommendationStatus.ACTIVE.value])
        )

        if options:
            q = q.options(*options)

        rows = q.all()
        return [self._to_entity(r) for r in rows]

    def list_all_for_user(self, session: Session, user_telegram_id: int) -> List[Recommendation]:
        user = UserRepository(session).find_by_telegram_id(user_telegram_id)
        if not user: return []
        rows = session.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(
            RecommendationORM.user_id == user.id
        ).order_by(RecommendationORM.created_at.desc()).all()
        return [self._to_entity(r) for r in rows]

    def list_all(self, session: Session, symbol: Optional[str] = None, status: Optional[str] = None) -> List[RecommendationORM]:
        q = session.query(RecommendationORM)
        if symbol: q = q.filter(RecommendationORM.asset == symbol.upper())
        if status: q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()).value)
        return q.order_by(RecommendationORM.created_at.desc()).all()

    def get_recent_assets_for_user(self, session: Session, user_telegram_id: int, limit: int = 5) -> List[str]:
        """Gets the most recently used asset symbols for a user."""
        user = UserRepository(session).find_by_telegram_id(user_telegram_id)
        if not user: return []
        
        results = session.query(RecommendationORM.asset).filter(
            RecommendationORM.user_id == user.id
        ).group_by(RecommendationORM.asset).order_by(
            desc(func.max(RecommendationORM.created_at))
        ).limit(limit).all()
        
        return [r[0] for r in results]

    def update_price_tracking(self, session: Session, rec_id: int, current_price: float):
        """Efficiently updates only the price tracking fields for a recommendation."""
        rec = session.query(RecommendationORM).filter(RecommendationORM.id == rec_id).first()
        if not rec: return

        if rec.highest_price_reached is None or current_price > rec.highest_price_reached:
            rec.highest_price_reached = current_price
        if rec.lowest_price_reached is None or current_price < rec.lowest_price_reached:
            rec.lowest_price_reached = current_price
    
    def get_published_messages(self, session: Session, rec_id: int) -> List[PublishedMessage]:
        """Fetches all publication records for a given recommendation."""
        return session.query(PublishedMessage).filter(PublishedMessage.recommendation_id == rec_id).all()

    def get_events_for_recommendations(self, session: Session, rec_ids: List[int]) -> Dict[int, Set[str]]:
        """Efficiently fetches all event types for a list of recommendations."""
        if not rec_ids: return {}
        
        rows = session.query(
            RecommendationEvent.recommendation_id, RecommendationEvent.event_type
        ).filter(RecommendationEvent.recommendation_id.in_(rec_ids)).all()
        
        result: Dict[int, Set[str]] = {rec_id: set() for rec_id in rec_ids}
        for rec_id, event_type in rows:
            result[rec_id].add(event_type)
            
        return result

# --- END OF FINAL, COMPLETE, AND CONCURRENCY-SAFE FILE ---```