# src/capitalguard/interfaces/telegram/helpers.py (RE-ARCHITECTED & FINAL)

import functools
import logging
from typing import TypeVar, Callable, Optional, List

from telegram.ext import ContextTypes
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import session_scope

log = logging.getLogger(__name__)
T = TypeVar('T')

def get_service(context: ContextTypes.DEFAULT_TYPE, service_name: str, service_type: type[T]) -> T:
    """
    A robust service getter that retrieves a service directly from the bot_data context.
    This is the new single source of truth for accessing services from handlers.
    """
    try:
        service = context.bot_data['services'][service_name]
        if not isinstance(service, service_type):
            raise TypeError(f"Service '{service_name}' is not of type '{service_type.__name__}'.")
        return service
    except KeyError:
        log.critical(
            "CRITICAL: Service '%s' could not be found in context.bot_data. Available services: %s",
            service_name, list(context.bot_data.get('services', {}).keys())
        )
        raise RuntimeError(f"Service '{service_name}' is unavailable.")

def unit_of_work(func: Callable) -> Callable:
    """
    A decorator for Telegram handlers that provides a database session (Unit of Work)
    for read-only operations or operations that don't use the transactional service methods.
    """
    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        with session_scope() as session:
            try:
                result = await func(update, context, db_session=session, *args, **kwargs)
                # Commit is handled by session_scope context manager
                return result
            except Exception as e:
                log.error(f"Exception in handler '{func.__name__}', transaction rolled back.", exc_info=True)
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