# --- START OF FILE: src/capitalguard/infrastructure/db/repository.py ---
import logging
from typing import List, Optional, Any, Union

import sqlalchemy as sa
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User
from .base import SessionLocal

log = logging.getLogger(__name__)


class RecommendationRepository:
    # -------------------------
    # Helpers
    # -------------------------
    @staticmethod
    def _coerce_enum(value: Any, enum_cls):
        """Return enum if already one, else cast from raw value/string."""
        if isinstance(value, enum_cls):
            return value
        return enum_cls(value)

    @staticmethod
    def _as_telegram_str(user_id: Optional[Union[int, str]]) -> Optional[str]:
        return None if user_id is None else str(user_id)

    @staticmethod
    def _placeholder_email(telegram_id: int) -> str:
        """Unique placeholder email to satisfy NOT NULL/UNIQUE constraints."""
        return f"tg{telegram_id}@telegram.local"

    def _to_entity(self, row: RecommendationORM) -> Recommendation:
        """
        Map ORM row -> Domain entity.
        Ensures domain.user_id is the Telegram ID (string) when relation is loaded.
        """
        status = self._coerce_enum(row.status, RecommendationStatus)
        order_type = self._coerce_enum(row.order_type, OrderType)
        side = self._coerce_enum(row.side, Side)

        telegram_user_id = None
        if getattr(row, "user", None) is not None:
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
    # Users
    # -------------------------
    def find_or_create_user(self, telegram_id: int, **kwargs) -> User:
        """
        Find a user by telegram_user_id or create if missing.
        Always provides a placeholder email to satisfy NOT NULL constraints.
        kwargs may include: username, first_name, last_name, email, user_type
        """
        with SessionLocal() as s:
            user: Optional[User] = (
                s.query(User).filter(User.telegram_user_id == telegram_id).first()
            )
            placeholder_email = kwargs.get("email") or self._placeholder_email(telegram_id)

            if user:
                # Backfill email and light profile fields if missing
                changed = False
                if not getattr(user, "email", None):
                    user.email = placeholder_email
                    changed = True
                for attr in ("username", "first_name", "last_name", "user_type"):
                    val = kwargs.get(attr)
                    if val and getattr(user, attr, None) != val:
                        setattr(user, attr, val)
                        changed = True
                if changed:
                    s.commit()
                    s.refresh(user)
                return user

            log.info("Creating new user for telegram_id=%s", telegram_id)
            new_user = User(
                telegram_user_id=telegram_id,
                email=placeholder_email,
                user_type=kwargs.get("user_type") or "trader",
                username=kwargs.get("username"),
                first_name=kwargs.get("first_name"),
                last_name=kwargs.get("last_name"),
            )
            s.add(new_user)
            s.commit()
            s.refresh(new_user)
            return new_user

    # -------------------------
    # Create
    # -------------------------
    def add(self, rec: Recommendation) -> Recommendation:
        """
        Adds a new Recommendation.
        Requires rec.user_id to be a Telegram ID (string/int), which we map to FK(User.id).
        """
        if not rec.user_id or not str(rec.user_id).isdigit():
            raise ValueError("A valid user_id (Telegram ID) is required to create a recommendation.")

        with SessionLocal() as s:
            try:
                # Ensure the owner exists; map to FK
                user = self.find_or_create_user(int(rec.user_id))

                row = RecommendationORM(
                    user_id=user.id,
                    asset=rec.asset.value,
                    side=self._coerce_enum(rec.side, Side).value,
                    entry=rec.entry.value,
                    stop_loss=rec.stop_loss.value,
                    targets=rec.targets.values,
                    order_type=self._coerce_enum(rec.order_type, OrderType),
                    status=self._coerce_enum(rec.status, RecommendationStatus),
                    channel_id=rec.channel_id,
                    message_id=rec.message_id,
                    published_at=rec.published_at,
                    market=rec.market,
                    notes=rec.notes,
                    activated_at=rec.activated_at,
                    alert_meta=rec.alert_meta,
                )
                s.add(row)
                s.commit()
                # Preload relation for correct domain.user_id
                s.refresh(row)
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception as e:
                log.error("❌ Failed to add recommendation. Rolling back. Error: %s", e, exc_info=True)
                s.rollback()
                raise

    # -------------------------
    # Read (scoped to user)
    # -------------------------
    def get_by_id_for_user(self, rec_id: int, user_telegram_id: Union[int, str]) -> Optional[Recommendation]:
        """Get a specific recommendation owned by the given Telegram user."""
        with SessionLocal() as s:
            user = self.find_or_create_user(int(user_telegram_id))
            row = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.id == rec_id, RecommendationORM.user_id == user.id)
                .first()
            )
            return self._to_entity(row) if row else None

    def list_open_for_user(
        self,
        user_telegram_id: Union[int, str],
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Recommendation]:
        """List open (PENDING/ACTIVE) recommendations scoped to a Telegram user with optional filters."""
        with SessionLocal() as s:
            user = self.find_or_create_user(int(user_telegram_id))
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

            if symbol:
                q = q.filter(RecommendationORM.asset.ilike(f"%{symbol.upper()}%"))
            if side:
                try:
                    q = q.filter(RecommendationORM.side == Side(side.upper()))
                except ValueError:
                    log.warning("Invalid side filter provided to list_open_for_user: %s", side)
            if status:
                try:
                    q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()))
                except ValueError:
                    log.warning("Invalid status filter provided to list_open_for_user: %s", status)

            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def list_all_for_user(
        self,
        user_telegram_id: Union[int, str],
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Recommendation]:
        """List all recommendations for a Telegram user with optional filters."""
        with SessionLocal() as s:
            user = self.find_or_create_user(int(user_telegram_id))
            q = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.user_id == user.id)
            )

            if symbol:
                q = q.filter(RecommendationORM.asset.ilike(f"%{symbol.upper()}%"))
            if status:
                try:
                    q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()))
                except ValueError:
                    log.warning("Invalid status filter provided to list_all_for_user: %s", status)

            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    # -------------------------
    # Read (global) — لأغراض إدارية إن لزم
    # -------------------------
    def get(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as s:
            row = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .get(rec_id)
            )
            return self._to_entity(row) if row else None

    def list_open(
        self,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Recommendation]:
        """Global open list with optional filters (admin/ops)."""
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

            if symbol:
                q = q.filter(RecommendationORM.asset.ilike(f"%{symbol.upper()}%"))
            if side:
                try:
                    q = q.filter(RecommendationORM.side == Side(side.upper()))
                except ValueError:
                    log.warning("Invalid side filter provided to list_open: %s", side)
            if status:
                try:
                    q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()))
                except ValueError:
                    log.warning("Invalid status filter provided to list_open: %s", status)

            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM).options(joinedload(RecommendationORM.user))
            if symbol:
                q = q.filter(RecommendationORM.asset.ilike(f"%{symbol.upper()}%"))
            if status:
                try:
                    q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()))
                except ValueError:
                    log.warning("Invalid status filter provided: %s", status)

            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    # -------------------------
    # Update
    # -------------------------
    def update(self, rec: Recommendation) -> Recommendation:
        """
        Update an existing recommendation.
        If rec.user_id is provided, we enforce ownership (Telegram user).
        """
        if rec.id is None:
            raise ValueError("Recommendation ID is required for update")

        with SessionLocal() as s:
            try:
                q = s.query(RecommendationORM).options(joinedload(RecommendationORM.user))
                if rec.user_id:
                    user = self.find_or_create_user(int(rec.user_id))
                    q = q.filter(RecommendationORM.id == rec.id, RecommendationORM.user_id == user.id)
                else:
                    q = q.filter(RecommendationORM.id == rec.id)

                row = q.first()
                if not row:
                    raise ValueError(f"Recommendation #{rec.id} not found or not owned by the user")

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
                # لا نغيّر المالك هنا
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
    # Insights
    # -------------------------
    def get_recent_assets_for_user(self, user_telegram_id: Union[str, int], limit: int = 5) -> List[str]:
        """Return most recently used unique assets for a Telegram user."""
        with SessionLocal() as s:
            user = self.find_or_create_user(int(user_telegram_id))
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
# --- END OF FILE ---