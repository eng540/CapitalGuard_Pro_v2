# --- START OF FINAL, COMPLETE, AND ARCHITECTURALLY-CORRECT FILE (Version 10.2.0) ---
# src/capitalguard/interfaces/telegram/helpers.py

import functools
import logging
from typing import TypeVar, Callable

from telegram.ext import ContextTypes

from capitalguard.service_registry import get_global_service
from capitalguard.infrastructure.db.base import SessionLocal

log = logging.getLogger(__name__)
T = TypeVar('T')

def get_service(context: ContextTypes.DEFAULT_TYPE, service_name: str, service_type: type[T]) -> T:
    """
    A robust service getter that retrieves a service from the global registry.
    This is the single source of truth for accessing application services.
    """
    service = get_global_service(service_name, service_type)
    
    if service is None:
        log.critical(
            "CRITICAL: Service '%s' of type '%s' could not be found. "
            "This means the application failed to initialize correctly.",
            service_name, service_type.__name__
        )
        raise RuntimeError(f"Service '{service_name}' is unavailable.")
        
    return service

def unit_of_work(func: Callable) -> Callable:
    """
    A decorator for Telegram handlers that provides a database session (Unit of Work).
    It correctly manages the session lifecycle: commit on success, rollback on failure.
    """
    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        """
        Wraps the handler function with a database session.
        """
        with SessionLocal() as session:
            try:
                # The session is injected as a keyword argument 'db_session' into the handler.
                result = await func(update, context, db_session=session, *args, **kwargs)
                # If the handler completes without raising an exception, all changes are committed.
                session.commit()
                return result
            except Exception as e:
                # If any exception occurs, all changes made within this session are rolled back.
                log.error(f"Exception in handler '{func.__name__}', rolling back transaction.", exc_info=True)
                session.rollback()
                # Re-raise the exception so it can be caught by PTB's global error handler for logging.
                raise e
    return wrapper

# --- END OF FINAL, COMPLETE, AND ARCHITECTURALLY-CORRECT FILE ---