from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def price_return_pct(entry: Any, exit_price: Any, side: Any) -> Decimal:
    """Canonical unleveraged price-return percentage.

    This metric is price return only. It is not exchange execution, realized
    monetary PnL, leverage-adjusted return, or portfolio return.
    """
    try:
        entry_dec = entry if isinstance(entry, Decimal) else Decimal(str(entry))
        exit_dec = exit_price if isinstance(exit_price, Decimal) else Decimal(str(exit_price))
        side_value = getattr(side, "value", side)
        side_upper = str(side_value or "").upper()
        if not entry_dec.is_finite() or entry_dec <= 0 or not exit_dec.is_finite():
            return Decimal("0")
        if side_upper == "LONG":
            return ((exit_dec - entry_dec) / entry_dec) * Decimal("100")
        if side_upper == "SHORT":
            return ((entry_dec - exit_dec) / entry_dec) * Decimal("100")
        return Decimal("0")
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return Decimal("0")
