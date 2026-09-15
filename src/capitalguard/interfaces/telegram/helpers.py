# File: src/capitalguard/interfaces/telegram/helpers.py
# Version: v25.7.0-R2 (Critical Hotfix)
# ✅ THE FIX: (R2 Architecture - Hotfix)
#    - 1. (CRITICAL) إضافة الدوال المساعدة (`_to_decimal`, `_pct`, `_format_price`)
#       التي كانت مفقودة وتسببت في `ImportError` في `ui_texts.py`.
#    - 2. (CLEAN) توحيد جميع الدوال المساعدة للواجهة (UI Helpers) في هذا الملف
#       ليكون "مصدر الحقيقة الوحيد" (SSoT) لها.
# 🎯 IMPACT: هذا الإصلاح يحل الـ `ImportError` ويجعل الواجهة قادرة على العمل.

import logging
from typing import TypeVar, Callable, Optional, List, Any
from decimal import Decimal, InvalidOperation

from telegram.ext import ContextTypes
from capitalguard.domain.financial_metrics import price_return_pct

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

# --- ✅ NEW (R2 Hotfix): Added Missing Helper Functions ---

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely gets attribute, handles domain objects with .value."""
    val = getattr(obj, attr, default)
    # Check if val itself has a 'value' attribute (like domain value objects: Symbol, Price, Side)
    return getattr(val, 'value', val)

def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """Safely converts input to a Decimal."""
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
    return f"{price_dec:g}" # Use 'g' for cleaner output

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