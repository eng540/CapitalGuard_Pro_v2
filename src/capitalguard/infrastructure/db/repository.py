# --- START OF FILE: src/capitalguard/infrastructure/db/repository.py ---
import logging
from typing import List, Optional, Any, Union

import sqlalchemy as sa
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User, Channel
from .base import SessionLocal

log = logging.getLogger(__name__)


# =========================
# User Repository (scoped)
# =========================
class UserRepository:
    """
    مستودع بسيط للتعامل مع مستخدمين تيليجرام داخل جلسة واحدة.
    يراعي المخطط الحالي: email NOT NULL/UNIQUE, hashed_password nullable, first_name optional.
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
        - يُبقي hashed_password = NULL (متوافق مع المخطط).
        - يملأ email بقيمة placeholder لاحترام UNIQUE/NOT NULL.
        - يُحدّث first_name و user_type إذا تم تمريرهما.
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
            # hashed_password => NULL by default (nullable=True)
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
    مستودع لقنوات تيليجرام المرتبطة بالمستخدمين (المحللين).
    يوفّر عمليات CRUD الأساسية مع حماية من الازدواجية بالـ ID أو الـ username.
    """
    def __init__(self, session: Session):
        self.session = session

    def find_by_username(self, username: str) -> Optional[Channel]:
        """بحث عام غير حساس لحالة الأحرف وبلا @ في بداية الاسم (غير مقيّد بالمستخدم)."""
        clean = (username or "").lstrip("@").lower()
        return (
            self.session.query(Channel)
            .filter(sa.func.lower(Channel.username) == clean)
            .first()
        )

    def list_by_user(self, user_id: int, only_active: bool = False) -> List[Channel]:
        """
        إرجاع قنوات المستخدم. استخدم only_active=True لفلترة القنوات المفعّلة فقط.
        """
        q = self.session.query(Channel).filter(Channel.user_id == user_id)
        if only_active:
            q = q.filter(Channel.is_active.is_(True))
        return q.order_by(Channel.created_at.desc()).all()

    def find_by_chat_id_for_user(self, user_id: int, chat_id: int) -> Optional[Channel]:
        """قناة مملوكة للمستخدم حسب telegram_channel_id."""
        return (
            self.session.query(Channel)
            .filter(Channel.user_id == user_id, Channel.telegram_channel_id == chat_id)
            .first()
        )

    def find_by_username_for_user(self, user_id: int, username: str) -> Optional[Channel]:
        """قناة مملوكة للمستخدم حسب username (بدون @ وغير حساس لحالة الأحرف)."""
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
        """
        ربط قناة بالمستخدم.
        - إن كانت القناة مرتبطة بنفس المستخدم: تُعاد كما هي (idempotent) مع تحديث العنوان إن تغيّر.
        - إن كانت مرتبطة بمستخدم آخر: يُرفع خطأ.
        - username اختياري (قنوات خاصة لا تملك username).
        """
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
                # Idempotent: تحديث title إن تغيّر
                updated = False
                if title and existing.title != title:
                    existing.title = title
                    updated = True
                if clean_username and not existing.username:
                    existing.username = clean_username  # ترقية: في حال أصبحت القناة عامة لاحقًا
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
        """تفعيل/تعطيل قناة مع التحقق من الملكية."""
        ch = (
            self.session.query(Channel)
            .filter(Channel.id == channel_id, Channel.user_id == user_id)
            .first()
        )
        if not ch:
            raise ValueError("Channel not found for this user.")
        ch.is_active = bool(is_active)
        self.session.commit()

    def remove(self, channel_id: int, user_id: int) -> None:
        """حذف ربط قناة مع التحقق من الملكية."""
        ch = (
            self.session.query(Channel)
            .filter(Channel.id == channel_id, Channel.user_id == user_id)
            .first()
        )
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
        """
        تحديث اختياري للعنوان/اسم المستخدم.
        - يطبع username بدون @ ويمنع التعارض مع قنوات أخرى.
        """
        ch = (
            self.session.query(Channel)
            .filter(Channel.id == channel_id, Channel.user_id == user_id)
            .first()
        )
        if not ch:
            raise ValueError("Channel not found for this user.")

        if username is not None:
            new_un = username.lstrip("@")
            new_un_lc = new_un.lower()
            if new_un:
                conflict = (
                    self.session.query(Channel)
                    .filter(sa.func.lower(Channel.username) == new_un_lc, Channel.id != ch.id)
                    .first()
                )
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
        """Return enum if already one, else cast from raw value/string."""
        if isinstance(value, enum_cls):
            return value
        return enum_cls(value)

    @staticmethod
    def _as_telegram_str(user_id: Optional[Union[int, str]]) -> Optional[str]:
        return None if user_id is None else str(user_id)

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
                user_repo = UserRepository(s)
                # نضمن وجود المالك حتى لو لم يرسل /start من قبل
                user = user_repo.find_or_create(int(rec.user_id))

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
                # تأكد من تحميل العلاقة user قبل التحويل إلى الدومين
                s.refresh(row)
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception as e:
                log.error("❌ Failed to add recommendation. Rolling back. Error: %s", e, exc_info=True)
                s.rollback()
                raise

    # -------------------------
    # Read (scoped to user via Telegram ID)
    # -------------------------
    def get_by_id_for_user(self, rec_id: int, user_telegram_id: Union[int, str]) -> Optional[Recommendation]:
        """Get a specific recommendation owned by the given Telegram user."""
        with SessionLocal() as s:
            user_repo = UserRepository(s)
            user = user_repo.find_by_telegram_id(int(user_telegram_id))
            if not user:
                return None
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
            user_repo = UserRepository(s)
            user = user_repo.find_by_telegram_id(int(user_telegram_id))
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
            user_repo = UserRepository(s)
            user = user_repo.find_by_telegram_id(int(user_telegram_id))
            if not user:
                return []

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
    # Read (scoped to user via *internal* user_id)
    # -------------------------
    def list_open_for_user_id(
        self,
        user_id: int,
        *,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Recommendation]:
        """قائمة التوصيات المفتوحة لمستخدم معيّن (حسب user_id الداخلي)."""
        with SessionLocal() as s:
            q = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(
                    RecommendationORM.user_id == user_id,
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
                    log.warning("Invalid side filter provided to list_open_for_user_id: %s", side)
            if status:
                try:
                    q = q.filter(RecommendationORM.status == RecommendationStatus(status.upper()))
                except ValueError:
                    log.warning("Invalid status filter provided to list_open_for_user_id: %s", status)

            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def list_all_for_user_id(self, user_id: int) -> List[Recommendation]:
        """كل توصيات المستخدم (حسب user_id الداخلي)."""
        with SessionLocal() as s:
            rows = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.user_id == user_id)
                .order_by(RecommendationORM.created_at.desc())
                .all()
            )
            return [self._to_entity(r) for r in rows]

    # -------------------------
    # Read (global) — لأغراض إدارية إن لزم
    # -------------------------
    def get(self, rec_id: int) -> Optional[Recommendation]:
        """Admin/global fetch by id (not scoped)."""
        with SessionLocal() as s:
            row = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.id == rec_id)
                .first()
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
        """Global all list with optional filters (admin/ops)."""
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
                    user_repo = UserRepository(s)
                    user = user_repo.find_by_telegram_id(int(rec.user_id))
                    if not user:
                        raise ValueError("Owner user not found.")
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
            user_repo = UserRepository(s)
            user = user_repo.find_by_telegram_id(int(user_telegram_id))
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
# --- END OF FILE ---