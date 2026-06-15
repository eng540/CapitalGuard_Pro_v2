# File: src/capitalguard/infrastructure/db/uow.py
# [STEP-1A] Master Engine — المصدر الوحيد لـ engine في النظام كله
#
# التغييرات:
#   1. دمج أفضل إعدادات الثلاثة engines السابقة في engine واحد
#   2. json_serializer لدعم Decimal في JSONB (من base.py)
#   3. prepare_threshold=None لـ Supabase PgBouncer (من base.py)
#   4. keepalives للاتصالات طويلة الأمد (من uow.py الأصلي)
#   5. pool_size=10, max_overflow=20 صريح (بدلاً من القيم الافتراضية)
#   6. isolated_session_scope() للـ AlertService background loop
#
# المعيار المالي:
#   في الأنظمة المالية، connection pool يُدار من نقطة واحدة.
#   تعدد الـ engines = تضارب المعاملات + استنزاف connections.

import json
import logging
from contextlib import contextmanager
from decimal import Decimal
from functools import wraps
from typing import Any, Callable, Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from telegram import Update
from telegram.ext import ContextTypes

from capitalguard.config import settings
from .models import Base
from .repository import UserRepository

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Custom JSON Serializer — يدعم Decimal في JSONB columns
# ─────────────────────────────────────────────────────────────────────────────

def _json_serializer(obj: Any) -> str:
    """
    Serializer مُخصَّص للـ engine.
    يُحوِّل Decimal → str لضمان التوافق مع PostgreSQL JSONB.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ─────────────────────────────────────────────────────────────────────────────
# Connection Arguments — يتكيّف مع SQLite (testing) و PostgreSQL (production)
# ─────────────────────────────────────────────────────────────────────────────

def _build_connect_args() -> dict:
    """
    يبني connect_args حسب نوع قاعدة البيانات.

    PostgreSQL (Supabase):
        - prepare_threshold=None: يُعطّل prepared statements.
          ضروري مع Supabase Transaction Pooler (PgBouncer) الذي
          لا يدعم server-side prepared statements.
        - keepalives: يُبقي الاتصالات حية خلال فترات الخمول.

    SQLite (testing):
        - check_same_thread=False: يسمح بالوصول من threads مختلفة.
    """
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return {"check_same_thread": False}

    return {
        "prepare_threshold":    None,  # Critical for Supabase PgBouncer
        "keepalives":           1,
        "keepalives_idle":      30,
        "keepalives_interval":  10,
        "keepalives_count":     5,
    }


# ─────────────────────────────────────────────────────────────────────────────
# [STEP-1A] THE ONE AND ONLY ENGINE
# ─────────────────────────────────────────────────────────────────────────────

try:
    log.info(
        "[STEP-1A] Initializing UNIFIED database engine: ...%s",
        settings.DATABASE_URL[-20:],
    )

    engine = create_engine(
        settings.DATABASE_URL,
        connect_args=_build_connect_args(),

        # JSON support for JSONB columns (Decimal → str)
        json_serializer=lambda obj: json.dumps(obj, default=_json_serializer),

        # Connection pool — صريح بدلاً من الافتراضي
        pool_size=10,        # اتصالات دائمة
        max_overflow=20,     # اتصالات إضافية تحت الضغط (مجموع 30)
        pool_timeout=30,     # انتظر 30 ثانية قبل TimeoutError
        pool_pre_ping=True,  # تحقق من الاتصال قبل الاستخدام
        pool_recycle=3600,   # أعد الاتصال كل ساعة لتجنب الانتهاء
    )

    # مصنع الجلسات الأساسي — يُستخدم لبناء كل session factories الأخرى
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    # Thread-safe scoped session — للـ Telegram handlers و AlertService
    SessionScoped = scoped_session(_session_factory)

    # Regular session factory — للـ FastAPI Depends() و isolated sessions
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    log.info(
        "[STEP-1A] Unified engine ready. "
        "pool_size=%d, max_overflow=%d, pool_recycle=%ds.",
        10, 20, 3600,
    )

except Exception as e:
    log.critical("[STEP-1A] FATAL: Failed to initialize database engine: %s", e, exc_info=True)
    raise


# ─────────────────────────────────────────────────────────────────────────────
# Table Creation
# ─────────────────────────────────────────────────────────────────────────────

def create_tables() -> None:
    """Creates all tables defined in models.py using the unified engine."""
    log.info("Creating database tables if they do not exist...")
    try:
        Base.metadata.create_all(engine)
        log.info("Database tables checked/created successfully.")
    except Exception as e:
        log.critical("Failed to create database tables: %s", e, exc_info=True)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# session_scope — للـ Telegram handlers والخدمات العامة
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Transactional scope باستخدام scoped_session (thread-local).
    مناسب لـ Telegram handlers وكل الخدمات التي تعمل في thread واحد.
    """
    session = SessionScoped()
    log.debug("session_scope: opened session %d.", id(session))
    try:
        yield session
        session.commit()
        log.debug("session_scope: committed session %d.", id(session))
    except Exception as e:
        log.error(
            "session_scope: rollback session %d — %s",
            id(session), e, exc_info=True,
        )
        session.rollback()
        raise
    finally:
        SessionScoped.remove()
        log.debug("session_scope: closed session %d.", id(session))


# ─────────────────────────────────────────────────────────────────────────────
# [STEP-1B] isolated_session_scope — للـ AlertService async workers
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def isolated_session_scope() -> Generator[Session, None, None]:
    """
    [STEP-1B] جلسة معزولة تماماً — لا تُشارك مع أي thread أو coroutine آخر.

    المشكلة التي تُعالجها:
        scoped_session يستخدم thread identity كـ registry key.
        في AlertService bg thread، جميع async workers تعمل في نفس thread،
        فتحصل كلها على نفس session من scoped_session.
        عند تنفيذ workers متزامنة: DetachedInstanceError، session corruption،
        وبيانات غير متسقة.

    المعيار المالي:
        كل معاملة مالية يجب أن تكون في جلسة مستقلة لضمان:
        - Isolation (ACID)
        - لا Dirty Reads بين workers متزامنة
        - لا تأثير جانبي بين صفقات مختلفة في نفس الـ thread
    """
    session = _session_factory()
    log.debug("isolated_session_scope: opened isolated session %d.", id(session))
    try:
        yield session
        session.commit()
        log.debug("isolated_session_scope: committed session %d.", id(session))
    except Exception as e:
        log.error(
            "isolated_session_scope: rollback session %d — %s",
            id(session), e, exc_info=True,
        )
        session.rollback()
        raise
    finally:
        session.close()
        log.debug("isolated_session_scope: closed session %d.", id(session))


# ─────────────────────────────────────────────────────────────────────────────
# uow_transaction — Decorator للـ PTB handlers
# ─────────────────────────────────────────────────────────────────────────────

def uow_transaction(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator لـ python-telegram-bot handlers.
    يُحقن db_session و db_user تلقائياً ويُدير commit/rollback.
    """
    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        session = SessionScoped()
        log.debug(
            "uow_transaction: session %d opened for handler '%s'.",
            id(session), func.__name__,
        )

        db_user = None
        if update and update.effective_user:
            try:
                db_user = UserRepository(session).find_by_telegram_id(
                    update.effective_user.id
                )
            except Exception as e:
                log.error(
                    "uow_transaction: failed to fetch db_user %s: %s",
                    update.effective_user.id, e, exc_info=True,
                )

        try:
            if "db_session" not in kwargs:
                kwargs["db_session"] = session
            if "db_user" not in kwargs:
                kwargs["db_user"] = db_user

            result = await func(update, context, *args, **kwargs)

            session.commit()
            log.debug(
                "uow_transaction: session %d committed for handler '%s'.",
                id(session), func.__name__,
            )
            return result

        except Exception as e:
            log.error(
                "uow_transaction: session %d rollback for handler '%s': %s",
                id(session), func.__name__, e, exc_info=True,
            )
            session.rollback()
            try:
                await update.effective_message.reply_text(
                    "An unexpected error occurred. The operation was cancelled."
                )
            except Exception:
                pass
            raise

        finally:
            SessionScoped.remove()
            log.debug(
                "uow_transaction: session %d closed for handler '%s'.",
                id(session), func.__name__,
            )

    return wrapper
