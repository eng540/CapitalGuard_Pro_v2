# --- START OF COMPLETE, LITERAL, AND FINAL FILE: src/capitalguard/infrastructure/db/repository.py ---
import logging
from datetime import datetime
from typing import List, Optional, Any, Union, Dict

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
    """
    Handles DB operations for User objects within a single session.
    """
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return (
            self.session.query(User)
            .filter(User.telegram_user_id == telegram_id)
            .first()
        )

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        """
        Finds a user by telegram_id or creates one if they don't exist.
        """
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
    """
    Handles DB operations for Channel objects within a single session.
    """
    def __init__(self, session: Session):
        self.session = session

    def find_by_username(self, username: str) -> Optional[Channel]:
        """Finds a channel by its username (case-insensitive)."""
        clean = (username or "").lstrip("@").lower()
        return (
            self.session.query(Channel)
            .filter(sa.func.lower(Channel.username) == clean)
            .first()
        )

    def list_by_user(self, user_id: int, only_active: bool = False) -> List[Channel]:
        """Lists all channels linked to a user."""
        q = self.session.query(Channel).filter(Channel.user_id == user_id)
        if only_active:
            q = q.filter(Channel.is_active.is_(True))
        return q.order_by(Channel.created_at.desc()).all()

    def find_by_chat_id_for_user(self, user_id: int, chat_id: int) -> Optional[Channel]:
        """Finds a channel owned by a user via its telegram_channel_id."""
        return (
            self.session.query(Channel)
            .filter(Channel.user_id == user_id, Channel.telegram_channel_id == chat_id)
            .first()
        )

    def find_by_username_for_user(self, user_id: int, username: str) -> Optional[Channel]:
        """Finds a channel owned by a user via its username."""
        clean = (username or "").lstrip("@").lower()
        return (
            self.session.query(Channel)
            .filter(Channel.user_id == user_id, sa.func.lower(Channel.username) == clean)
            .first()
        )

    def add(
        self,
        user_id: int,
        telegram_channel_id: int,
        username: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Channel:
        """Links a channel to a user, preventing duplicates."""
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
                # Idempotent: Update metadata if changed
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
        """Activates or deactivates a channel, checking for ownership."""
        ch = self.find_by_chat_id_for_user(user_id, channel_id)
        if not ch:
            raise ValueError("Channel not found for this user.")
        ch.is_active = bool(is_active)
        self.session.commit()

    def remove(self, channel_id: int, user_id: int) -> None:
        """Unlinks a channel, checking for ownership."""
        ch = self.find_by_chat_id_for_user(user_id, channel_id)
        if not ch:
            raise ValueError("Channel not found for this user.")
        self.session.delete(ch)
        self.session.commit()

    def update_metadata(
        self,
        channel_id: int,
        user_id: int,
        *,
        title: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Channel:
        """Updates channel metadata, checking for ownership."""
        ch = self.find_by_chat_id_for_user(user_id, channel_id)
        if not ch:
            raise ValueError("Channel not found for this user.")

        if username is not None:
            new_un = username.lstrip("@")
            new_un_lc = new_un.lower()
            if new_un:
                conflict = self.session.query(Channel).filter(sa.func.lower(Channel.username) == new_un_lc, Channel.id != ch.id).first()
                if conflict:
                    raise ValueError("Username is already used by another linked channel.")
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
        return value if isinstance(value, enum_cls) else enum_cls(value)

    @staticmethod
    def _as_telegram_str(user_id: Optional[Union[int, str]]) -> Optional[str]:
        return str(user_id) if user_id is not None else None

    def _to_entity(self, row: RecommendationORM) -> Optional[Recommendation]:
        if not row:
            return None
        
        status = self._coerce_enum(row.status, RecommendationStatus)
        order_type = self._coerce_enum(row.order_type, OrderType)
        side = self._coerce_enum(row.side, Side)

        telegram_user_id = None
        if getattr(row, "user", None):
            telegram_user_id = self._as_telegram_str(row.user.telegram_user_id)

        return Recommendation(
            id=row.id,
            asset=Symbol(row.asset),
            side=side,
            entry=Price(row.entry),
            stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])),
            order_type=order_type,
            status=status,
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
        )

    # -------------------------
    # Create
    # -------------------------
    def add(self, rec: Recommendation) -> Recommendation:
        if not rec.user_id or not str(rec.user_id).isdigit():
            raise ValueError("A valid user_id (Telegram ID) is required to create a recommendation.")
        with SessionLocal() as s:
            user = UserRepository(s).find_or_create(int(rec.user_id))
            row = RecommendationORM(
                user_id=user.id, asset=rec.asset.value, side=rec.side.value,
                entry=rec.entry.value, stop_loss=rec.stop_loss.value, targets=rec.targets.values,
                order_type=rec.order_type, status=rec.status,
                market=rec.market, notes=rec.notes, activated_at=rec.activated_at,
                alert_meta=rec.alert_meta,
            )
            s.add(row)
            s.commit()
            s.refresh(row, attribute_names=["user"])
            return self._to_entity(row)

    # -------------------------
    # Read Methods
    # -------------------------
    def get(self, rec_id: int) -> Optional[Recommendation]:
        """Global fetch by id (not user-scoped)."""
        with SessionLocal() as s:
            row = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(RecommendationORM.id == rec_id).first()
            return self._to_entity(row)

    def get_by_id_for_user(self, rec_id: int, user_telegram_id: Union[int, str]) -> Optional[Recommendation]:
        """User-scoped fetch by id, ensuring ownership."""
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(int(user_telegram_id))
            if not user: return None
            row = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(RecommendationORM.id == rec_id, RecommendationORM.user_id == user.id).first()
            return self._to_entity(row)

    def list_open_for_user(self, user_telegram_id: Union[int, str], **filters) -> List[Recommendation]:
        """User-scoped list of open recommendations with optional filters."""
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(int(user_telegram_id))
            if not user: return []
            q = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(
                RecommendationORM.user_id == user.id,
                or_(RecommendationORM.status == RecommendationStatus.PENDING, RecommendationORM.status == RecommendationStatus.ACTIVE)
            )
            if filters.get("symbol"): q = q.filter(RecommendationORM.asset.ilike(f'%{filters["symbol"].upper()}%'))
            if filters.get("side"): q = q.filter(RecommendationORM.side == Side(filters["side"].upper()).value)
            if filters.get("status"): q = q.filter(RecommendationORM.status == RecommendationStatus(filters["status"].upper()))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    def list_open(self, **filters) -> List[Recommendation]:
        """
        Global list of all open recommendations, not scoped to a user.
        Used by backend services like Watcher and AlertService.
        """
        with SessionLocal() as s:
            q = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(
                or_(
                    RecommendationORM.status == RecommendationStatus.PENDING,
                    RecommendationORM.status == RecommendationStatus.ACTIVE
                )
            )
            if filters.get("symbol"):
                q = q.filter(RecommendationORM.asset.ilike(f'%{filters["symbol"].upper()}%'))
            if filters.get("side"):
                q = q.filter(RecommendationORM.side == Side(filters["side"].upper()).value)
            if filters.get("status"):
                q = q.filter(RecommendationORM.status == RecommendationStatus(filters["status"].upper()))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    def list_all(self, **filters) -> List[Recommendation]:
        """Global list of all recommendations with optional filters."""
        with SessionLocal() as s:
            q = s.query(RecommendationORM).options(joinedload(RecommendationORM.user))
            if filters.get("symbol"): q = q.filter(RecommendationORM.asset.ilike(f'%{filters["symbol"].upper()}%'))
            if filters.get("status"): q = q.filter(RecommendationORM.status == RecommendationStatus(filters["status"].upper()))
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    def get_recent_assets_for_user(self, user_telegram_id: Union[str, int], limit: int = 5) -> List[str]:
        with SessionLocal() as s:
            user = UserRepository(s).find_by_telegram_id(int(user_telegram_id))
            if not user: return []
            subq = s.query(RecommendationORM.asset, sa.func.max(RecommendationORM.created_at).label("max_created_at")).filter(RecommendationORM.user_id == user.id).group_by(RecommendationORM.asset).subquery()
            results = s.query(subq.c.asset).order_by(subq.c.max_created_at.desc()).limit(limit).all()
            return [r[0] for r in results]

    # -------------------------
    # Update
    # -------------------------
    def update(self, rec: Recommendation) -> Recommendation:
        if rec.id is None: raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            row = s.query(RecommendationORM).filter(RecommendationORM.id == rec.id).first()
            if not row: raise ValueError(f"Recommendation #{rec.id} not found")
            
            row.asset = rec.asset.value; row.side = rec.side.value
            row.entry = rec.entry.value; row.stop_loss = rec.stop_loss.value
            row.targets = rec.targets.values; row.order_type = rec.order_type
            row.status = rec.status; row.channel_id = rec.channel_id
            row.message_id = rec.message_id; row.published_at = rec.published_at
            row.market = rec.market; row.notes = rec.notes
            row.exit_price = rec.exit_price; row.activated_at = rec.activated_at
            row.closed_at = rec.closed_at; row.alert_meta = rec.alert_meta
            
            s.commit()
            s.refresh(row, attribute_names=["user"])
            return self._to_entity(row)

    # -------------------------
    # New Functions for Multi-Channel Support
    # -------------------------
    def get_published_messages(self, rec_id: int) -> List[PublishedMessage]:
        """Fetches all published message metadata for a given recommendation."""
        with SessionLocal() as s:
            return s.query(PublishedMessage).filter(PublishedMessage.recommendation_id == rec_id).all()

    def save_published_messages(self, messages_data: List[Dict[str, Any]]) -> None:
        """Bulk saves new published message records."""
        if not messages_data: return
        with SessionLocal() as s:
            s.bulk_insert_mappings(PublishedMessage, messages_data)
            s.commit()

    def update_legacy_publication_fields(self, rec_id: int, first_pub_data: Dict[str, Any]) -> None:
        """Updates the legacy channel_id/message_id fields for backward compatibility."""
        with SessionLocal() as s:
            s.query(RecommendationORM).filter(RecommendationORM.id == rec_id).update({
                'channel_id': first_pub_data['telegram_channel_id'],
                'message_id': first_pub_data['telegram_message_id'],
                'published_at': datetime.now(timezone.utc)
            })
            s.commit()
# --- END OF COMPLETE, LITERAL, AND FINAL FILE ---