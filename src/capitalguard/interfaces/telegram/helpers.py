# src/capitalguard/interfaces/telegram/helpers.py (v25.4 - FINAL & DECOUPLED)
"""
Provides helper functions for Telegram handlers, primarily for service access.
The uow_transaction decorator is now imported directly by handlers that need it.
"""

import logging
from typing import TypeVar, Callable, Optional, List

from telegram.ext import ContextTypes

log = logging.getLogger(__name__)
T = TypeVar('T')

def get_service(context: ContextTypes.DEFAULT_TYPE, service_name: str, service_type: type[T]) -> T:
    """A robust service getter that retrieves a service from the bot_data context."""
    try:
        service = context.bot_data['services'][service_name]
        if not isinstance(service, service_type):
            raise TypeError(f"Service '{service_name}' is not of type '{service_type.__name__}'.")
        return service
    except KeyError:
        log.critical(
            "CRITICAL: Service '%s' could not be found. Available services: %s",
            service_name, list(context.bot_data.get('services', {}).keys())
        )
        raise RuntimeError(f"Service '{service_name}' is unavailable.")

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

#END