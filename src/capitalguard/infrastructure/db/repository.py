# --- START OF CORRECTED AND FINAL FILE: src/capitalguard/infrastructure/db/repository.py ---
import logging
# ✅ --- FIX: Import List and Dict from typing ---
from typing import List, Optional, Any, Union, Dict
# --- END OF FIX ---

import sqlalchemy as sa
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User, Channel, PublishedMessage
from .base import SessionLocal

log = logging.getLogger(__name__)


# =========================
# User Repository (scoped)
# =========================
class UserRepository:
    # ... (No changes in this class)
    def __init__(self, session: Session):
        self.session = session
    # ... (Rest of the class is unchanged)
    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return (
            self.session.query(User)
            .filter(User.telegram_user_id == telegram_id)
            .first()
        )

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        user = self.find_by_telegram_id(telegram_id)
        placeholder_email = kwargs.get("email") or f"tg{telegram_id}@telegram.local"

        if user:
            changed = False
            if not getattr(user, "email", None):
                user.email = placeholder_email
                changed = True
            ut = kwargs.get("user_type")
            if ut and getattr(user, "user_type", None) != ut:
                user.user_type = ut
                changed = True
            if getattr(user, "is_active", True) is False:
                user.is_active = True
                changed = True
            fn = kwargs.get("first_name")
            if fn is not None and getattr(user, "first_name", None) != fn:
                user.first_name = fn
                changed = True

            if changed:
                self.session.commit()
                self.session.refresh(user)
            return user

        log.info("Creating new user for telegram_id=%s", telegram_id)
        new_user = User(
            telegram_user_id=telegram_id,
            email=placeholder_email,
            user_type=(kwargs.get("user_type") or "trader"),
            is_active=True,
            first_name=kwargs.get("first_name"),
        )
        self.session.add(new_user)
        self.session.commit()
        self.session.refresh(new_user)
        return new_user

# =========================
# Channel Repository (scoped)
# =========================
class ChannelRepository:
    # ... (No changes in this class)
    def __init__(self, session: Session):
        self.session = session
    # ... (Rest of the class is unchanged)
    def find_by_username(self, username: str) -> Optional[Channel]:
        clean = (username or "").lstrip("@").lower()
        return (
            self.session.query(Channel)
            .filter(sa.func.lower(Channel.username) == clean)
            .first()
        )

    def list_by_user(self, user_id: int, only_active: bool = False) -> List[Channel]:
        q = self.session.query(Channel).filter(Channel.user_id == user_id)
        if only_active:
            q = q.filter(Channel.is_active.is_(True))
        return q.order_by(Channel.created_at.desc()).all()

    def find_by_chat_id_for_user(self, user_id: int, chat_id: int) -> Optional[Channel]:
        return (
            self.session.query(Channel)
            .filter(Channel.user_id == user_id, Channel.telegram_channel_id == chat_id)
            .first()
        )

    def find_by_username_for_user(self, user_id: int, username: str) -> Optional[Channel]:
        clean = (username or "").lstrip("@").lower()
        return (
            self.session.query(Channel)
            .filter(Channel.user_id == user_id, sa.func.lower(Channel.username) == clean)
            .first()
        )

    def add(self, user_id: int, telegram_channel_id: int, username: Optional[str] = None, title: Optional[str] = None) -> Channel:
        clean_username = (username or "").lstrip("@")
        clean_username_lc = clean_username.lower() if clean_username else None

        existing = (
            self.session.query(Channel)
            .filter(
                or_(
                    Channel.telegram_channel_id == telegram_channel_id,
                    sa.func.lower(Channel.username) == clean_username_lc if clean_username_lc else sa.false(),
                )
            )
            .first()
        )
        if existing:
            if existing.user_id == user_id:
                updated = False
                if title and existing.title != title:
                    existing.title = title
                    updated = True
                if clean_username and not existing.username:
                    existing.username = clean_username
                    updated = True
                if updated:
                    self.session.commit()
                    self.session.refresh(existing)
                return existing
            raise ValueError(f"Channel '{username or telegram_channel_id}' is already linked by another user.")

        new_ch = Channel(
            user_id=user_id,
            telegram_channel_id=telegram_channel_id,
            username=clean_username or None,
            title=title,
            is_active=True,
        )
        self.session.add(new_ch)
        self.session.commit()
        self.session.refresh(new_ch)
        log.info(
            "Linked channel '%s' (id=%s) to user_id=%s",
            clean_username or "-", telegram_channel_id, user_id
        )
        return new_ch

    def set_active(self, channel_id: int, user_id: int, is_active: bool) -> None:
        ch = (
            self.session.query(Channel)
            .filter(Channel.id == channel_id, Channel.user_id == user_id)
            .first()
        )
        if not ch: raise ValueError("Channel not found for this user.")
        ch.is_active = bool(is_active)
        self.session.commit()

    def remove(self, channel_id: int, user_id: int) -> None:
        ch = (
            self.session.query(Channel)
            .filter(Channel.id == channel_id, Channel.user_id == user_id)
            .first()
        )
        if not ch: raise ValueError("Channel not found for this user.")
        self.session.delete(ch)
        self.session.commit()

    def update_metadata(self, channel_id: int, user_id: int, *, title: Optional[str] = None, username: Optional[str] = None) -> Channel:
        ch = (
            self.session.query(Channel)
            .filter(Channel.id == channel_id, Channel.user_id == user_id)
            .first()
        )
        if not ch: raise ValueError("Channel not found for this user.")

        if username is not None:
            new_un = username.lstrip("@")
            new_un_lc = new_un.lower()
            if new_un:
                conflict = (
                    self.session.query(Channel)
                    .filter(sa.func.lower(Channel.username) == new_un_lc, Channel.id != ch.id)
                    .first()
                )
                if conflict: raise ValueError("Username is already used by another linked channel.")
                ch.username = new_un
            else:
                ch.username = None

        if title is not None:
            ch.title = title

        self.session.commit()
        self.session.refresh(ch)
        return ch

# =========================
# Recommendation Repository
# =========================
class RecommendationRepository:
    # -------------------------
    # Helpers
    # -------------------------
    @staticmethod
    def _coerce_enum(value: Any, enum_cls):
        if isinstance(value, enum_cls):
            return value
        return enum_cls(value)

    @staticmethod
    def _as_telegram_str(user_id: Optional[Union[int, str]]) -> Optional[str]:
        return None if user_id is None else str(user_id)

    def _to_entity(self, row: RecommendationORM) -> Recommendation:
        status = self._coerce_enum(row.status, RecommendationStatus)
        order_type = self._coerce_enum(row.order_type, OrderType)
        side = self._coerce_enum(row.side, Side)

        telegram_user_id = None
        if getattr(row, "user", None) is not None:
            telegram_user_id = self._as_telegram_str(row.user.telegram_user_id)

        return Recommendation(
            id=row.id, asset=Symbol(row.asset), side=side, entry=Price(row.entry),
            stop_loss=Price(row.stop_loss), targets=Targets(list(row.targets or [])),
            order_type=order_type, status=status, channel_id=row.channel_id,
            message_id=row.message_id, published_at=row.published_at, market=row.market,
            notes=row.notes, user_id=telegram_user_id, created_at=row.created_at,
            updated_at=row.updated_at, exit_price=row.exit_price,
            activated_at=row.activated_at, closed_at=row.closed_at,
            alert_meta=dict(row.alert_meta or {}),
        )

    # -------------------------
    # Create
    # -------------------------
    def add(self, rec: Recommendation) -> Recommendation:
        if not rec.user_id or not str(rec.user_id).isdigit():
            raise ValueError("A valid user_id (Telegram ID) is required to create a recommendation.")

        with SessionLocal() as s:
            try:
                user_repo = UserRepository(s)
                user = user_repo.find_or_create(int(rec.user_id))
                row = RecommendationORM(
                    user_id=user.id, asset=rec.asset.value, side=self._coerce_enum(rec.side, Side).value,
                    entry=rec.entry.value, stop_loss=rec.stop_loss.value, targets=rec.targets.values,
                    order_type=self._coerce_enum(rec.order_type, OrderType),
                    status=self._coerce_enum(rec.status, RecommendationStatus),
                    channel_id=rec.channel_id, message_id=rec.message_id,
                    published_at=rec.published_at, market=rec.market,
                    notes=rec.notes, activated_at=rec.activated_at,
                    alert_meta=rec.alert_meta,
                )
                s.add(row)
                s.commit()
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception as e:
                log.error("❌ Failed to add recommendation. Rolling back. Error: %s", e, exc_info=True)
                s.rollback()
                raise

    # -------------------------
    # Read
    # -------------------------
    def get(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as s:
            row = s.query(RecommendationORM).filter(RecommendationORM.id == rec_id).first()
            return self._to_entity(row) if row else None

    def list_open(self, symbol: Optional[str] = None, side: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM).filter(or_(
                RecommendationORM.status == RecommendationStatus.PENDING,
                RecommendationORM.status == RecommendationStatus.ACTIVE,
            ))
            if symbol: q = q.filter(RecommendationORM.asset.ilike(f"%{symbol.upper()}%"))
            if side: q = q.filter(RecommendationORM.side == Side(side.upper()).value)
            if status: q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM)
            if symbol: q = q.filter(RecommendationORM.asset.ilike(f"%{symbol.upper()}%"))
            if status: q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    # -------------------------
    # Update
    # -------------------------
    def update(self, rec: Recommendation) -> Recommendation:
        if rec.id is None: raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            try:
                row = s.query(RecommendationORM).filter(RecommendationORM.id == rec.id).first()
                if not row: raise ValueError(f"Recommendation #{rec.id} not found")
                
                row.asset = rec.asset.value
                row.side = self._coerce_enum(rec.side, Side).value
                row.entry = rec.entry.value
                row.stop_loss = rec.stop_loss.value
                row.targets = rec.targets.values
                row.order_type = self._coerce_enum(rec.order_type, OrderType)
                row.status = self._coerce_enum(rec.status, RecommendationStatus)
                row.channel_id = rec.channel_id
                row.message_id = rec.message_id
                row.published_at = rec.published_at
                row.market = rec.market
                row.notes = rec.notes
                row.exit_price = rec.exit_price
                row.activated_at = rec.activated_at
                row.closed_at = rec.closed_at
                row.alert_meta = rec.alert_meta
                
                s.commit()
                s.refresh(row)
                return self._to_entity(row)
            except Exception as e:
                log.error("❌ Failed to update recommendation #%s. Rolling back. Error: %s", rec.id, e, exc_info=True)
                s.rollback()
                raise

    # -------------------------
    # New Functions for Multi-Channel Support
    # -------------------------
    def get_published_messages(self, rec_id: int) -> List[PublishedMessage]:
        """Fetches all published message metadata for a given recommendation."""
        with SessionLocal() as s:
            return s.query(PublishedMessage).filter(PublishedMessage.recommendation_id == rec_id).all()

    def save_published_messages(self, messages_data: List[Dict[str, Any]]) -> None:
        """Bulk saves new published message records."""
        with SessionLocal() as s:
            s.bulk_insert_mappings(PublishedMessage, messages_data)
            s.commit()

    def update_legacy_publication_fields(self, rec_id: int, first_pub_data: Dict[str, Any]) -> None:
        """Updates the legacy channel_id/message_id fields on the recommendation for compatibility."""
        with SessionLocal() as s:
            s.query(RecommendationORM).filter(RecommendationORM.id == rec_id).update({
                'channel_id': first_pub_data['telegram_channel_id'],
                'message_id': first_pub_data['telegram_message_id'],
                'published_at': sa.func.now()
            })
            s.commit()

    # -------------------------
    # Insights
    # -------------------------
    def get_recent_assets_for_user(self, user_telegram_id: Union[str, int], limit: int = 5) -> List[str]:
        """Return most recently used unique assets for a Telegram user."""
        with SessionLocal() as s:
            user_repo = UserRepository(s)
            user = user_repo.find_by_telegram_id(int(user_telegram_id))
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
# --- END OF CORRECTED AND FINAL FILE ---