# File: src/capitalguard/infrastructure/db/uow.py
# Version: v2.x (Original File)
# ✅ THE FIX: (Original File)
#    - 1. هذا الملف هو "وحدة العمل" (Unit of Work) الأساسية.
# 🎯 IMPACT: مطلوب بواسطة جميع المعالجات (Handlers) التي تبدأ بـ `@uow_transaction`.

import logging
from contextlib import contextmanager
from functools import wraps
from typing import Optional, Generator, Any, Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, scoped_session
from telegram import Update
from telegram.ext import ContextTypes

from capitalguard.config import settings
from .models import Base
from .repository import UserRepository

log = logging.getLogger(__name__)

# --- Database Engine & Session Setup ---

try:
    log.info(f"Initializing database engine for URL: ...{settings.DATABASE_URL[-20:]}")
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )
    
    # Create a thread-safe, scoped session factory
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    SessionScoped = scoped_session(_session_factory)
    log.info("Database engine and scoped session factory initialized successfully.")

except Exception as e:
    log.critical(f"Failed to initialize database engine: {e}", exc_info=True)
    # This is a fatal error, the application cannot run.
    raise

def create_tables():
    """Creates all tables defined in models.py."""
    log.info("Creating database tables if they do not exist...")
    try:
        Base.metadata.create_all(engine)
        log.info("Database tables checked/created successfully.")
    except Exception as e:
        log.critical(f"Failed to create database tables: {e}", exc_info=True)
        raise

# --- Unit of Work (UoW) Context Manager ---

@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Provide a transactional scope around a series of operations.
    This handles session creation, commit, rollback, and closing.
    """
    session = SessionScoped()
    log.debug(f"Session {id(session)} opened.")
    try:
        yield session
        session.commit()
        log.debug(f"Session {id(session)} committed.")
    except Exception as e:
        log.error(f"Session {id(session)} rollback due to exception: {e}", exc_info=True)
        session.rollback()
        raise
    finally:
        SessionScoped.remove()
        log.debug(f"Session {id(session)} closed and removed.")

# --- PTB Handler Decorator ---

def uow_transaction(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator for python-telegram-bot handlers to inject a clean db_session
    and handle commit/rollback automatically.
    
    It also injects the `db_user` object if `require_active_user`
    is not present (for fallback).
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> Any:
        session = SessionScoped()
        log.debug(f"UoW Decorator: Session {id(session)} opened for handler {func.__name__}")
        
        db_user = None
        if update and update.effective_user:
            try:
                db_user = UserRepository(session).find_by_telegram_id(update.effective_user.id)
            except Exception as e:
                log.error(f"UoW: Failed to fetch db_user {update.effective_user.id}: {e}", exc_info=True)
                # Don't fail the handler, just pass None
        
        try:
            # Inject db_session and db_user into kwargs if not already present
            # (This allows other decorators like @require_active_user to run first
            # and provide their own db_user if needed)
            if 'db_session' not in kwargs:
                kwargs['db_session'] = session
            if 'db_user' not in kwargs:
                kwargs['db_user'] = db_user
                
            result = await func(update, context, *args, **kwargs)
            
            session.commit()
            log.debug(f"UoW Decorator: Session {id(session)} committed for handler {func.__name__}.")
            return result
        
        except Exception as e:
            log.error(f"UoW Decorator: Session {id(session)} rollback for handler {func.__name__} due to: {e}", exc_info=True)
            session.rollback()
            
            # Try to inform the user of the error
            try:
                await update.effective_message.reply_text(
                    "An unexpected error occurred. The operation was cancelled. "
                    "The admin has been notified."
                )
            except Exception as notify_e:
                log.error(f"Failed to notify user of handler error: {notify_e}")
                
            # (Rethrow the exception so PTB's error handler can log it)
            raise
            
        finally:
            SessionScoped.remove()
            log.debug(f"UoW Decorator: Session {id(session)} closed and removed for handler {func.__name__}.")

    return wrapper