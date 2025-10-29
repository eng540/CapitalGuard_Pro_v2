# --- src/capitalguard/interfaces/telegram/ui_texts.py ---
# src/capitalguard/interfaces/telegram/ui_texts.py (v29.0 - Decoupled)
"""
Contains helper functions for building UI text content *outside* of the core notifier.
✅ HOTFIX: Removed `build_trade_card_text` and all its related helpers
(like _calculate_weighted_pnl, _build_header, _build_summary_section, etc.)
as they have been moved to `infrastructure/notify/telegram.py` to break
the circular import dependency.
- This file now only contains helpers for the interactive conversation flows.
"""

from __future__ import annotations
import logging
from typing import List, Optional, Dict, Any
from decimal import Decimal, InvalidOperation
from datetime import datetime

# ❌ REMOVED: Imports related to trade_service or complex domain logic
# from capitalguard.application.services.trade_service import TradeService (REMOVED)

log = logging.getLogger(__name__)

# --- Core Helpers (Still needed for review_text) ---

def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """Safely convert any value to a Decimal, returning default on failure."""
    if isinstance(value, Decimal): return value if value.is_finite() else default
    if value is None: return default
    try: d = Decimal(str(value)); return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError): return default

def _format_price(price: Any) -> str:
    """Formats a Decimal or number into a clean string (e.g., no trailing zeros)."""
    price_dec = _to_decimal(price)
    return "N/A" if not price_dec.is_finite() else f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    """Computes percentage PnL from entry to target_price."""
    entry_dec, target_dec = _to_decimal(entry), _to_decimal(target_price)
    if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite(): return 0.0
    # Use simple string comparison, _get_attr is not needed here
    side_upper = (str(side) or "").upper()
    try:
        if side_upper == "LONG": pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT": pnl = ((entry_dec / target_dec) - 1) * 100
        else: return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError): return 0.0

def _format_pnl(pnl: float) -> str:
    """Formats a PnL float into a string like '+5.23%'."""
    return f"{pnl:+.2f}%"

# ❌ REMOVED: All functions related to build_trade_card_text
# (_get_attr, _rr, _calculate_weighted_pnl, _get_result_text, _build_header,
# _build_live_price_section, _build_performance_section, _build_exit_plan_section,
# _build_logbook_section, _build_summary_section, build_trade_card_text)
# These now live in infrastructure/notify/telegram.py

# --- Build Review Text (Used by conversation_handlers) ---

def build_review_text_with_price(draft: dict, preview_price: Optional[float]) -> str:
    """Builds the review text for the interactive recommendation builder."""
    asset = draft.get("asset", "N/A")
    side = draft.get("side", "N/A")
    market = draft.get("market", "Futures")
    entry = _to_decimal(draft.get("entry", 0))
    sl = _to_decimal(draft.get("stop_loss", 0))
    raw_tps = draft.get("targets", [])
    
    target_lines = []
    for i, t in enumerate(raw_tps, start=1):
        price = _to_decimal(t.get('price', 0))
        pct_value = _pct(entry, price, side)
        close_percent = t.get('close_percent', 0)
        suffix = f" (Close {close_percent:.0f}%)" if 0 < close_percent < 100 else ""
        if close_percent == 100 and i == len(raw_tps): suffix = ""
        
        target_lines.append(f"  • TP{i}: <code>{_format_price(price)}</code> ({_format_pnl(pct_value)}){suffix}")

    base_text = (
        f"📝 <b>REVIEW RECOMMENDATION</b>\n"
        f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        f"<b>{asset} | {market} / {side}</b>\n\n"
        f"💰 Entry: <code>{_format_price(entry)}</code>\n"
        f"🛑 Stop: <code>{_format_price(sl)}</code>\n"
        f"🎯 Targets:\n" + "\n".join(target_lines) + "\n"
    )
    
    if preview_price is not None:
        base_text += f"\n💹 Current Price: <code>{_format_price(preview_price)}</code>"
    
    base_text += "\n\nReady to publish?"
    return base_text
# --- END OF FILE ---