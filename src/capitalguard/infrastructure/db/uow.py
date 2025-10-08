# src/capitalguard/infrastructure/db/uow.py (v25.5 - FINAL & STABLE)
"""
Provides a transactional unit of work scope for database operations.
"""

import logging
import inspect
from functools import wraps
from contextlib import contextmanager
from typing import Callable

from sqlalchemy.orm import Session
from .base import SessionLocal

log = logging.getLogger(__name__)

@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def uow_transaction(func: Callable) -> Callable:
    """
    A decorator for Telegram handlers that provides a database session (Unit of Work).
    It ensures that the decorated function runs within a single, atomic database transaction.
    """
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        with session_scope() as session:
            try:
                result = await func(update, context, db_session=session, *args, **kwargs)
                return result
            except Exception as e:
                log.error(f"Exception in handler '{func.__name__}', transaction rolled back.", exc_info=True)
                raise e
    return wrapper

#END