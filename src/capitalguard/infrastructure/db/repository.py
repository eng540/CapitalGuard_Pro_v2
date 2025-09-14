import logging
from datetime import datetime, timezone
from typing import List, Optional, Any, Union, Dict

import sqlalchemy as sa
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, Session
from sqlalchemy.exc import IntegrityError

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
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
    """
    واجهة قنوات تيليجرام (قابلة للاستخدام direto مع Handlers الربط):
      - add(owner_user_id, telegram_channel_id, title=None, username=None, notes=None, is_active=True) -> Channel
      - get_by_telegram_channel_id(tg_id) -> Optional[Channel]
      - list_by_user(user_id, only_active: bool=False) -> List[Channel]
      - set_active(user_id, telegram_channel_id, active: bool) -> None
      - update_title_username(telegram_channel_id, title, username) -> Channel
    ملاحظة: النموذج Channel (بحسب ملفك) لا يحتوي can_post، لذلك لم نستخدمه هنا.
    """

    def __init__(self, session: Session):
        self.session = session

    # --- Queries ---
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

    # --- Mutations ---
    def add(
        self,
        owner_user_id: int,
        telegram_channel_id: int,
        *,
        title: Optional[str] = None,
        username: Optional[str] = None,
        notes: Optional[str] = None,
        is_active: bool = True,
    ) -> Channel:
        """
        ينشئ (أو يُحدّث) ربط قناة تيليجرام بمستخدم مالك.
        - يضمن عدم التكرار عبر telegram_channel_id.
        - يُحدّث الحقول (title/username/notes/is_active/last_verified_at) عند وجود سجل سابق.
        - في حال وجود القناة بمالك مختلف، سيتم نقل الملكية إلى owner_user_id (قرار عملي لتبسيط تجربة الربط).
        """
        ch = self.get_by_telegram_channel_id(telegram_channel_id)
        now = datetime.now(timezone.utc)

        if ch:
            # تحديث القناة القائمة
            if ch.user_id != owner_user_id:
                log.warning(
                    "Reassigning channel %s ownership from user_id=%s to user_id=%s",
                    telegram_channel_id, ch.user_id, owner_user_id
                )
                ch.user_id = owner_user_id

            if title:
                ch.title = title
            if username is not None:
                ch.username = username or None
            if notes is not None:
                ch.notes = notes or None

            ch.is_active = bool(is_active)
            ch.last_verified_at = now

            try:
                self.session.commit()
            except IntegrityError as ie:
                self.session.rollback()
                # احتمال تعارض username unique:
                raise ValueError(f"Username is already used by another channel: {username}") from ie

            self.session.refresh(ch)
            return ch

        # إنشاء جديد
        ch = Channel(
            user_id=owner_user_id,
            telegram_channel_id=telegram_channel_id,
            username=username or None,
            title=title or None,
            is_active=bool(is_active),
            notes=notes or None,
            last_verified_at=now,
        )
        self.session.add(ch)
        try:
            self.session.commit()
        except IntegrityError as ie:
            self.session.rollback()
            raise ValueError("Failed to add channel (integrity error). Possible duplicate username or tg_id.") from ie

        self.session.refresh(ch)
        return ch

    def set_active(self, owner_user_id: int, telegram_channel_id: int, active: bool) -> None:
        ch = (
            self.session.query(Channel)
            .filter(
                Channel.user_id == owner_user_id,
                Channel.telegram_channel_id == telegram_channel_id,
            )
            .first()
        )
        if not ch:
            raise ValueError("Channel not found for this user.")
        ch.is_active = bool(active)
        ch.last_verified_at = datetime.now(timezone.utc)
        self.session.commit()

    def update_title_username(
        self, telegram_channel_id: int, *, title: Optional[str] = None, username: Optional[str] = None
    ) -> Channel:
        ch = self.get_by_telegram_channel_id(telegram_channel_id)
        if not ch:
            raise ValueError("Channel not found.")
        if title is not None:
            ch.title = title
        if username is not None:
            ch.username = username or None
        ch.last_verified_at = datetime.now(timezone.utc)
        try:
            self.session.commit()
        except IntegrityError as ie:
            self.session.rollback()
            raise ValueError("Username already in use by another channel.") from ie
        self.session.refresh(ch)
        return ch


# -----------------------------
# Recommendations
# -----------------------------
class RecommendationRepository:
    @staticmethod
    def _coerce_enum(value: Any, enum_cls):
        return value if isinstance(value, enum_cls) else enum_cls(value)

    @staticmethod
    def _as_telegram_str(user_id: Optional[Union[int, str]]) -> Optional[str]:
        return str(user_id) if user_id is not None else None

    def _to_entity(self, row: RecommendationORM) -> Optional[Recommendation]:
        if not row:
            return None
        telegram_user_id = self._as_telegram_str(row.user.telegram_user_id) if getattr(row, "user", None) else None
        return Recommendation(
            id=row.id,
            asset=Symbol(row.asset),
            side=self._coerce_enum(row.side, Side),
            entry=Price(row.entry),
            stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])),
            order_type=self._coerce_enum(row.order_type, OrderType),
            status=self._coerce_enum(row.status, RecommendationStatus),
            channel_id=row.channel_id,
            message_id=row.message_id,
            published_at=row.published_at,
            market=row.market,
            notes=row.notes,
            user_id=telegram_user_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            exit_price=row.exit_price,
            activated_at=row.activated_at,
            closed_at=row.closed_at,
            alert_meta=dict(row.alert_meta or {}),
            highest_price_reached=row.highest_price_reached,
            lowest_price_reached=row.lowest_price_reached,
            open_size_percent=row.open_size_percent,
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
                    alert_meta=rec.alert_meta,
                    highest_price_reached=rec.entry.value if rec.status == RecommendationStatus.ACTIVE else None,
                    lowest_price_reached=rec.entry.value if rec.status == RecommendationStatus.ACTIVE else None,
                    open_size_percent=rec.open_size_percent,
                )
                s.add(row)
                s.flush()
                create_event = RecommendationEvent(
                    recommendation_id=row.id,
                    event_type='CREATE',
                    event_timestamp=row.created_at,
                    event_data={
                        'entry': rec.entry.value,
                        'sl': rec.stop_loss.value,
                        'targets': targets_for_db,
                        'order_type': rec.order_type.value
                    }
                )
                s.add(create_event)
                s.commit()
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception as e:
                s.rollback()
                log.error("Failed to add recommendation with event: %s", e, exc_info=True)
                raise

    def update_with_event(self, rec: Recommendation, event_type: str, event_data: Dict[str, Any]) -> Recommendation:
        if rec.id is None:
            raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            try:
                row = s.query(RecommendationORM).filter(RecommendationORM.id == rec.id).first()
                if not row:
                    raise ValueError(f"Recommendation #{rec.id} not found")
                targets_for_db = [v.__dict__ for v in rec.targets.values]
                row.status = rec.status
                row.stop_loss = rec.stop_loss.value
                row.targets = targets_for_db
                row.notes = rec.notes
                row.exit_price = rec.exit_price
                row.activated_at = rec.activated_at
                row.closed_at = rec.closed_at
                row.alert_meta = rec.alert_meta
                row.highest_price_reached = rec.highest_price_reached
                row.lowest_price_reached = rec.lowest_price_reached
                row.open_size_percent = rec.open_size_percent
                new_event = RecommendationEvent(
                    recommendation_id=row.id,
                    event_type=event_type,
                    event_data=event_data
                )
                s.add(new_event)
                s.commit()
                s.refresh(row)
                return self._to_entity(row)
            except Exception as e:
                s.rollback()
                log.error("Failed to update recommendation with event: %s", e, exc_info=True)
                raise

    def update(self, rec: Recommendation) -> Recommendation:
        if rec.id is None:
            raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            try:
                row = s.query(RecommendationORM).filter(RecommendationORM.id == rec.id).first()
                if not row:
                    raise ValueError(f"Recommendation #{rec.id} not found")
                row.alert_meta = rec.alert_meta
                row.highest_price_reached = rec.highest_price_reached
                row.lowest_price_reached = rec.lowest_price_reached
                s.commit()
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception as e:
                s.rollback()
                log.error("Failed to perform simple update: %s", e, exc_info=True)
                raise

    def get(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as s:
            row = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.id == rec_id)
                .first()
            )
            return self._to_entity(row)

    def get_by_id_for_user(self, rec_id: int, user_telegram_id: Union[int, str]) -> Optional[Recommendation]:
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(int(user_telegram_id))
            if not user:
                return None
            row = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.id == rec_id, RecommendationORM.user_id == user.id)
                .first()
            )
            return self._to_entity(row)

    def list_open(self, **filters) -> List[Recommendation]:
        with SessionLocal() as s:
            q = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(
                    or_(
                        RecommendationORM.status == RecommendationStatus.PENDING,
                        RecommendationORM.status == RecommendationStatus.ACTIVE,
                    )
                )
            )
            if filters.get("symbol"):
                q = q.filter(RecommendationORM.asset.ilike(f'%{filters["symbol"].upper()}%'))
            if filters.get("status"):
                q = q.filter(RecommendationORM.status == self._coerce_enum(filters["status"], RecommendationStatus))
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
                    or_(
                        RecommendationORM.status == RecommendationStatus.PENDING,
                        RecommendationORM.status == RecommendationStatus.ACTIVE,
                    ),
                )
            )
            if filters.get("symbol"):
                q = q.filter(RecommendationORM.asset.ilike(f'%{filters["symbol"].upper()}%'))
            if filters.get("side"):
                q = q.filter(RecommendationORM.side == Side(filters["side"].upper()).value)
            if filters.get("status"):
                q = q.filter(RecommendationORM.status == self._coerce_enum(filters["status"], RecommendationStatus))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    def get_recent_assets_for_user(self, user_telegram_id: Union[str, int], limit: int = 5) -> List[str]:
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(int(user_telegram_id))
            if not user:
                return []
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

    def check_if_event_exists(self, rec_id: int, event_type: str) -> bool:
        with SessionLocal() as s:
            return (
                s.query(RecommendationEvent.id)
                .filter_by(recommendation_id=rec_id, event_type=event_type)
                .first()
                is not None
            )

    def get_published_messages(self, rec_id: int) -> List[PublishedMessage]:
        with SessionLocal() as s:
            return (
                s.query(PublishedMessage)
                .filter(PublishedMessage.recommendation_id == rec_id)
                .all()
            )

    def save_published_messages(self, messages_data: List[Dict[str, Any]]) -> None:
        if not messages_data:
            return
        with SessionLocal() as s:
            s.bulk_insert_mappings(PublishedMessage, messages_data)
            s.commit()

    # ✅ FIX: removed stray bracket, kept your intent
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

    def list_active_by_symbol(self, symbol: str) -> List[Recommendation]:
        with SessionLocal() as s:
            rows = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(
                    RecommendationORM.asset == symbol.upper(),
                    RecommendationORM.status == RecommendationStatus.ACTIVE,
                )
                .all()
            )
            return [self._to_entity(r) for r in rows]

    def log_events_bulk(self, events_data: List[Dict[str, Any]]):
        if not events_data:
            return
        with SessionLocal() as s:
            s.bulk_insert_mappings(RecommendationEvent, events_data)
            s.commit()