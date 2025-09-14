import logging
from datetime import datetime, timezone
from typing import List, Optional, Any, Union, Dict

import sqlalchemy as sa
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, Session
from sqlalchemy.exc import IntegrityError

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User, Channel, PublishedMessage, RecommendationEvent
from .base import SessionLocal

log = logging.getLogger(__name__)

# -----------------------------
# Users
# -----------------------------
class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return (
            self.session.query(User)
            .filter(User.telegram_user_id == telegram_id)
            .first()
        )

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        user = self.find_by_telegram_id(telegram_id)
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
        self.session.add(new_user)
        self.session.commit()
        self.session.refresh(new_user)
        return new_user


# -----------------------------
# Channels
# -----------------------------
class ChannelRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_telegram_channel_id(self, telegram_channel_id: int) -> Optional[Channel]:
        return (
            self.session.query(Channel)
            .filter(Channel.telegram_channel_id == telegram_channel_id)
            .first()
        )

    def list_by_user(self, user_id: int, only_active: bool = False) -> List[Channel]:
        q = self.session.query(Channel).filter(Channel.user_id == user_id)
        if only_active:
            q = q.filter(Channel.is_active.is_(True))
        return q.order_by(Channel.created_at.desc()).all()

    def add(self, owner_user_id: int, telegram_channel_id: int, **kwargs) -> Channel:
        ch = self.get_by_telegram_channel_id(telegram_channel_id)
        now = datetime.now(timezone.utc)
        if ch:
            ch.user_id = owner_user_id
            ch.title = kwargs.get("title", ch.title)
            ch.username = kwargs.get("username", ch.username)
            ch.is_active = kwargs.get("is_active", ch.is_active)
            ch.last_verified_at = now
        else:
            ch = Channel(
                user_id=owner_user_id,
                telegram_channel_id=telegram_channel_id,
                username=kwargs.get("username"),
                title=kwargs.get("title"),
                is_active=kwargs.get("is_active", True),
                last_verified_at=now,
            )
            self.session.add(ch)
        try:
            self.session.commit()
            self.session.refresh(ch)
            return ch
        except IntegrityError as e:
            self.session.rollback()
            log.error("Failed to add or update channel due to integrity error: %s", e)
            raise ValueError("Username is already in use by another channel.") from e


# -----------------------------
# Recommendations
# -----------------------------
class RecommendationRepository:
    @staticmethod
    def _to_entity(row: RecommendationORM) -> Optional[Recommendation]:
        if not row:
            return None
        
        user_telegram_id = str(row.user.telegram_user_id) if getattr(row, "user", None) else None

        return Recommendation(
            id=row.id,
            asset=Symbol(row.asset),
            side=Side(row.side),
            entry=Price(row.entry),
            stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])),
            order_type=OrderType(row.order_type),
            status=RecommendationStatus(row.status),
            market=row.market,
            notes=row.notes,
            user_id=user_telegram_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            exit_price=row.exit_price,
            activated_at=row.activated_at,
            closed_at=row.closed_at,
            alert_meta=dict(row.alert_meta or {}),
            highest_price_reached=row.highest_price_reached,
            lowest_price_reached=row.lowest_price_reached,
            exit_strategy=ExitStrategy(row.exit_strategy),
            profit_stop_price=row.profit_stop_price,
            open_size_percent=row.open_size_percent,
            events=row.events,
        )

    def add_with_event(self, rec: Recommendation) -> Recommendation:
        if not rec.user_id or not str(rec.user_id).isdigit():
            raise ValueError("A valid user_id (Telegram ID) is required.")
        with SessionLocal() as s:
            try:
                user = UserRepository(s).find_or_create(int(rec.user_id))
                targets_for_db = [v.__dict__ for v in rec.targets.values]
                row = RecommendationORM(
                    user_id=user.id,
                    asset=rec.asset.value,
                    side=rec.side.value,
                    entry=rec.entry.value,
                    stop_loss=rec.stop_loss.value,
                    targets=targets_for_db,
                    order_type=rec.order_type,
                    status=rec.status,
                    market=rec.market,
                    notes=rec.notes,
                    activated_at=rec.activated_at,
                    exit_strategy=rec.exit_strategy,
                    profit_stop_price=rec.profit_stop_price,
                    open_size_percent=rec.open_size_percent,
                )
                s.add(row)
                s.flush()
                
                create_event = RecommendationEvent(
                    recommendation_id=row.id,
                    event_type='CREATE',
                    event_timestamp=row.created_at,
                    event_data={'entry': rec.entry.value, 'sl': rec.stop_loss.value}
                )
                s.add(create_event)
                s.commit()
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception:
                s.rollback()
                log.exception("Failed to add recommendation with event.")
                raise

    def update_with_event(self, rec: Recommendation, event_type: str, event_data: Dict[str, Any]) -> Recommendation:
        if rec.id is None: raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            try:
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

                new_event = RecommendationEvent(
                    recommendation_id=row.id, event_type=event_type, event_data=event_data
                )
                s.add(new_event)
                s.commit()
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception:
                s.rollback()
                log.exception("Failed to update recommendation with event.")
                raise

    def update(self, rec: Recommendation) -> Recommendation:
        if rec.id is None: raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            try:
                row = s.query(RecommendationORM).filter(RecommendationORM.id == rec.id).first()
                if not row: raise ValueError(f"Recommendation #{rec.id} not found")
                
                row.highest_price_reached = rec.highest_price_reached
                row.lowest_price_reached = rec.lowest_price_reached
                
                s.commit()
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception:
                s.rollback()
                log.exception("Failed to perform simple update on recommendation.")
                raise

    def get(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as s:
            # ✅ FIX: Changed 'rec.id' to the correct parameter name 'rec_id'.
            row = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.id == rec_id)
                .first()
            )
            return self._to_entity(row)

    def list_open(self) -> List[Recommendation]:
        with SessionLocal() as s:
            rows = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.status.in_([RecommendationStatus.PENDING, RecommendationStatus.ACTIVE]))
                .order_by(RecommendationORM.created_at.desc())
                .all()
            )
            return [self._to_entity(r) for r in rows]

    def list_open_by_symbol(self, symbol: str) -> List[Recommendation]:
        with SessionLocal() as s:
            rows = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(
                    RecommendationORM.asset == symbol.upper(),
                    RecommendationORM.status.in_([RecommendationStatus.PENDING, RecommendationStatus.ACTIVE])
                )
                .all()
            )
            return [self._to_entity(r) for r in rows]

    def get_events_for_recommendations(self, rec_ids: List[int]) -> Dict[int, set[str]]:
        if not rec_ids:
            return {}
        with SessionLocal() as s:
            results = s.query(
                RecommendationEvent.recommendation_id,
                RecommendationEvent.event_type
            ).filter(
                RecommendationEvent.recommendation_id.in_(rec_ids)
            ).all()
        event_map = {}
        for rec_id, event_type in results:
            event_map.setdefault(rec_id, set()).add(event_type)
        return event_map

    def save_published_messages(self, messages_data: List[Dict[str, Any]]) -> None:
        if not messages_data: return
        with SessionLocal() as s:
            s.bulk_insert_mappings(PublishedMessage, messages_data)
            s.commit()

    def get_published_messages(self, rec_id: int) -> List[PublishedMessage]:
        with SessionLocal() as s:
            return (
                s.query(PublishedMessage)
                .filter(PublishedMessage.recommendation_id == rec_id)
                .all()
            )

    def get_recent_assets_for_user(self, user_telegram_id: Union[str, int], limit: int = 5) -> List[str]:
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(int(user_telegram_id))
            if not user: return []
            subq = (
                s.query(
                    RecommendationORM.asset,
                    sa.func.max(RecommendationORM.created_at).label("max_created_at"),
                )
                .filter(RecommendationORM.user_id == user.id)
                .group_by(RecommendationORM.asset)
                .subquery()
            )
            results = (
                s.query(subq.c.asset)
                .order_by(subq.c.max_created_at.desc())
                .limit(limit)
                .all()
            )
            return [r[0] for r in results]

    def list_all_for_user(self, user_id: int, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(RecommendationORM.user_id == user_id)
            if symbol:
                q = q.filter(RecommendationORM.asset.ilike(f'%{symbol.upper()}%'))
            if status:
                q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    def list_open_for_user(self, user_telegram_id: Union[int, str], **filters) -> List[Recommendation]:
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(int(user_telegram_id))
            if not user:
                return []
            q = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(
                    RecommendationORM.user_id == user.id,
                    RecommendationORM.status.in_([RecommendationStatus.PENDING, RecommendationStatus.ACTIVE]),
                )
            )
            if filters.get("symbol"):
                q = q.filter(RecommendationORM.asset.ilike(f'%{filters["symbol"].upper()}%'))
            if filters.get("side"):
                q = q.filter(RecommendationORM.side == Side(filters["side"].upper()).value)
            if filters.get("status"):
                q = q.filter(RecommendationORM.status == RecommendationStatus(filters["status"].upper()))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    def update_legacy_publication_fields(self, rec_id: int, first_pub_data: Dict[str, Any]) -> None:
        with SessionLocal() as s:
            s.query(RecommendationORM).filter(RecommendationORM.id == rec_id).update(
                {
                    'channel_id': first_pub_data['telegram_channel_id'],
                    'message_id': first_pub_data['telegram_message_id'],
                    'published_at': datetime.now(timezone.utc),
                }
            )
            s.commit()