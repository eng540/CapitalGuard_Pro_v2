# src/capitalguard/interfaces/telegram/ui_texts.py (v25.9 - COMPLETE, FINAL & PRODUCTION-READY)
"""
Contains helper functions for building the text content of Telegram messages.
This is a complete, final, and production-ready file.
"""

from __future__ import annotations
from typing import List, Optional, Dict, Any
from decimal import Decimal, InvalidOperation

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.domain.value_objects import Target

_STATUS_MAP = {
    RecommendationStatus.PENDING: "⏳ PENDING",
    RecommendationStatus.ACTIVE: "⚡️ ACTIVE",
    RecommendationStatus.CLOSED: "🏁 CLOSED",
}
_SIDE_ICONS = {'LONG': '🟢', 'SHORT': '🔴'}

def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    if isinstance(value, Decimal): return value
    try: return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError): return default

def _format_price(price: Any) -> str:
    price_dec = _to_decimal(price)
    return f"{price_dec:g}" if price_dec.is_finite() else "N/A"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    entry_dec, target_dec = _to_decimal(entry), _to_decimal(target_price)
    if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite(): return 0.0
    side_upper = (side or "").upper()
    try:
        if side_upper == "LONG": pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT": pnl = ((entry_dec / target_dec) - 1) * 100
        else: return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError): return 0.0

def _format_pnl(pnl: float) -> str:
    return f"{pnl:+.2f}%"

def _rr(entry: Any, sl: Any, first_target: Optional[Target]) -> str:
    try:
        entry_dec, sl_dec = _to_decimal(entry), _to_decimal(sl)
        if first_target is None or not entry_dec.is_finite() or not sl_dec.is_finite(): return "—"
        risk = abs(entry_dec - sl_dec)
        if risk.is_zero(): return "∞"
        reward = abs(_to_decimal(first_target.price.value) - entry_dec)
        ratio = reward / risk
        return f"1:{ratio:.2f}"
    except Exception: return "—"

def _calculate_weighted_pnl(rec: Recommendation) -> float:
    if rec.status == RecommendationStatus.CLOSED and rec.exit_price is not None:
        return _pct(rec.entry.value, rec.exit_price, rec.side.value)
    return 0.0

def _get_result_text(pnl: float) -> str:
    if pnl > 0.001: return "🏆 WIN"
    elif pnl < -0.001: return "💔 LOSS"
    else: return "🛡️ BREAKEVEN"

def _build_header(rec: Recommendation) -> str:
    status_text = _STATUS_MAP.get(rec.status, "UNKNOWN")
    side_icon = _SIDE_ICONS.get(rec.side.value, '⚪')
    id_prefix = "Trade" if getattr(rec, 'is_user_trade', False) else "Signal"
    return f"<b>{status_text} | #{rec.asset.value} | {rec.side.value}</b> {side_icon} | {id_prefix} #{rec.id}"

def _build_live_price_section(rec: Recommendation) -> str:
    live_price = getattr(rec, "live_price", None)
    if rec.status != RecommendationStatus.ACTIVE or live_price is None: return ""
    pnl = _pct(rec.entry.value, live_price, rec.side.value)
    pnl_icon = '🟢' if pnl >= 0 else '🔴'
    return "\n".join([
        "─ ─ ─ ─ ─ ─ ─ ─ ─ ─",
        f"💹 <b>Live Price:</b> <code>{_format_price(live_price)}</code> ({pnl_icon} {_format_pnl(pnl)})",
        "─ ─ ─ ─ ─ ─ ─ ─ ─ ─"
    ])

def _build_performance_section(rec: Recommendation) -> str:
    entry_price, stop_loss = rec.entry.value, rec.stop_loss.value
    sl_pnl = _pct(entry_price, stop_loss, rec.side.value)
    first_target = rec.targets.values[0] if rec.targets.values else None
    return "\n".join([
        "📊 <b>PERFORMANCE</b>",
        f"💰 Entry: <code>{_format_price(entry_price)}</code>",
        f"🛑 Stop: <code>{_format_price(stop_loss)}</code> ({_format_pnl(sl_pnl)})",
        f"💡 Risk/Reward (Plan): ~<code>{_rr(entry_price, stop_loss, first_target)}</code>"
    ])

def _build_exit_plan_section(rec: Recommendation) -> str:
    lines = ["\n🎯 <b>EXIT PLAN</b>"]
    entry_price = rec.entry.value
    for i, target in enumerate(rec.targets.values, start=1):
        pct_value = _pct(entry_price, target.price.value, rec.side.value)
        lines.append(f"  • TP{i}: <code>{_format_price(target.price.value)}</code> ({_format_pnl(pct_value)})")
    return "\n".join(lines)

def _build_summary_section(rec: Recommendation) -> str:
    pnl = _calculate_weighted_pnl(rec)
    return "\n".join([
        "📊 <b>TRADE SUMMARY</b>",
        f"💰 Entry: <code>{_format_price(rec.entry.value)}</code>",
        f"🏁 Final Exit Price: <code>{_format_price(rec.exit_price)}</code>",
        f"{'📈' if pnl >= 0 else '📉'} <b>Final Result: {_format_pnl(pnl)}</b> ({_get_result_text(pnl)})",
    ])

def build_trade_card_text(rec: Recommendation) -> str:
    header = _build_header(rec)
    parts = [header]
    section_builders = {
        RecommendationStatus.PENDING: [_build_performance_section, _build_exit_plan_section],
        RecommendationStatus.ACTIVE: [_build_live_price_section, _build_performance_section, _build_exit_plan_section],
        RecommendationStatus.CLOSED: [_build_summary_section]
    }
    for builder in section_builders.get(rec.status, []):
        if section := builder(rec): parts.append(section)
    if rec.notes: parts.append(f"\n📝 <b>Notes:</b> <i>{rec.notes}</i>")
    return "\n".join(filter(None, parts))

def build_review_text_with_price(draft: dict, preview_price: Optional[float]) -> str:
    asset, side, market = draft.get("asset", "N/A"), draft.get("side", "N/A"), draft.get("market", "Futures")
    entry, sl = draft.get("entry", Decimal(0)), draft.get("stop_loss", Decimal(0))
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
    if preview_price is not None: base_text += f"\n💹 Current Price: <code>{_format_price(preview_price)}</code>"
    base_text += "\n\nReady to publish?"
    return base_text