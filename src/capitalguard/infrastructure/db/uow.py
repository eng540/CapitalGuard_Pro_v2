# --- START OF NEW, COMPLETE, AND PRODUCTION-READY FILE ---
# src/capitalguard/infrastructure/db/uow.py

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
    A decorator for service methods that ensures they run within a single,
    atomic database transaction (Unit of Work).

    It supports both sync and async functions. If the decorated function is
    called with a 'db_session' keyword argument, it reuses that session.
    Otherwise, it creates a new session scope for the duration of the call.
    """
    is_coro = inspect.iscoroutinefunction(func)

    if is_coro:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if 'db_session' in kwargs and isinstance(kwargs['db_session'], Session):
                return await func(*args, **kwargs)
            
            with session_scope() as session:
                # Pass the session as a keyword argument to the decorated function
                return await func(*args, db_session=session, **kwargs)
        return async_wrapper
    else: # Sync function
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if 'db_session' in kwargs and isinstance(kwargs['db_session'], Session):
                return func(*args, **kwargs)

            with session_scope() as session:
                return func(*args, db_session=session, **kwargs)
        return sync_wrapper

# --- END OF NEW, COMPLETE, AND PRODUCTION-READY FILE ---