# --- START OF FINAL, PRODUCTION-READY FILE (Version 15.3.0) ---
# src/capitalguard/interfaces/telegram/helpers.py

import functools
import logging
from typing import TypeVar, Callable, Optional, List

from telegram.ext import ContextTypes

from capitalguard.service_registry import get_global_service
from capitalguard.infrastructure.db.base import SessionLocal

log = logging.getLogger(__name__)
T = TypeVar('T')

def get_service(context: ContextTypes.DEFAULT_TYPE, service_name: str, service_type: type[T]) -> T:
    """
    A robust service getter that retrieves a service from the global registry.
    """
    service = get_global_service(service_name, service_type)
    if service is None:
        log.critical(
            "CRITICAL: Service '%s' of type '%s' could not be found.",
            service_name, service_type.__name__
        )
        raise RuntimeError(f"Service '{service_name}' is unavailable.")
    return service

def unit_of_work(func: Callable) -> Callable:
    """
    A decorator for Telegram handlers that provides a database session (Unit of Work)
    for read-only operations or operations that don't use the transactional service methods.
    """
    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        with SessionLocal() as session:
            try:
                # This decorator is now primarily for handlers that need to READ from the DB.
                # Write operations should be delegated to transactional service methods.
                result = await func(update, context, db_session=session, *args, **kwargs)
                session.commit() # Commit is safe for read-only or simple write operations.
                return result
            except Exception as e:
                log.error(f"Exception in handler '{func.__name__}', rolling back transaction.", exc_info=True)
                session.rollback()
                # Re-raise the exception to be caught by the global error handler
                raise e
    return wrapper

def parse_tail_int(data: str) -> Optional[int]:
    """Safely parses the last integer from a colon-separated string."""
    try:
        return int(data.split(":")[-1])
    except (ValueError, IndexError, AttributeError):
        return None

def parse_cq_parts(data: str) -> List[str]:
    """Safely splits a callback query data string by colons."""
    if not isinstance(data, str):
        return []
    return data.split(":")

# --- END OF FINAL, PRODUCTION-READY FILE (Version 15.3.0) ---