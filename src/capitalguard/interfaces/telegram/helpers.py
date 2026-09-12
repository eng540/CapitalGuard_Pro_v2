# File: src/capitalguard/interfaces/telegram/helpers.py
# Version: v25.8.0-FINANCIAL-REALITY

import logging
from typing import TypeVar, Callable, Optional, List, Any
from decimal import Decimal, InvalidOperation

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

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely gets attribute, handles domain objects with .value."""
    val = getattr(obj, attr, default)
    return getattr(val, 'value', val)

def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """Safely converts input to a finite Decimal."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    if value is None:
        return default
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError):
        return default

def _format_price(price: Any) -> str:
    """Formats a Decimal or number into a clean string, handling N/A."""
    price_dec = _to_decimal(price)
    if not price_dec.is_finite() or price_dec == Decimal(0):
        return "N/A"
    return f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    """Canonical unleveraged price-return percentage.

    This metric is price return only. It is not realized monetary PnL,
    portfolio return, leverage-adjusted return, or fee/funding-adjusted PnL.

    LONG:  ((exit - entry) / entry) * 100
    SHORT: ((entry - exit) / entry) * 100
    """
    try:
        entry_dec = _to_decimal(entry)
        exit_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not exit_dec.is_finite():
            return 0.0

        side_upper = (getattr(side, 'value', side) or "").upper()
        if side_upper == "LONG":
            pnl = ((exit_dec - entry_dec) / entry_dec) * 100
        elif side_upper == "SHORT":
            pnl = ((entry_dec - exit_dec) / entry_dec) * 100
        else:
            return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError):
        return 0.0

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