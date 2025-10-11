# src/capitalguard/infrastructure/db/uow.py (v25.6 - Transaction Fix)
"""
Provides a transactional unit of work scope for database operations.
This version includes a critical fix to the uow_transaction decorator to
ensure database commits are correctly handled.
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
        # ✅ THE FIX: Implement a robust try/except/finally block for transaction management.
        # This guarantees that either a commit or a rollback occurs, and the session is always closed.
        session = SessionLocal()
        try:
            # Pass the session to the decorated handler.
            result = await func(update, context, db_session=session, *args, **kwargs)
            # If the handler completes without raising an exception, commit the transaction.
            session.commit()
            return result
        except Exception as e:
            # If any exception occurs, roll back all changes made during this transaction.
            log.error(f"Exception in handler '{func.__name__}', rolling back transaction.", exc_info=True)
            session.rollback()
            # Re-raise the exception so it can be handled by PTB's global error handler.
            raise e
        finally:
            # Always close the session to release the database connection.
            session.close()
    return wrapper