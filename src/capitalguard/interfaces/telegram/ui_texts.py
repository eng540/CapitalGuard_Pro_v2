# src/capitalguard/interfaces/telegram/ui_texts.py (v28.2 - Final & Production Ready)
"""
Contains helper functions for building the text content of Telegram messages.
This is the final, complete, and reliable version, featuring event-driven
rendering for the logbook, accurate weighted PnL calculations, and robust
number formatting. This file is 100% complete.
"""

from __future__ import annotations
from typing import List, Optional, Dict, Any
from decimal import Decimal, InvalidOperation
from datetime import datetime

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
    if not price_dec.is_finite():
        return "N/A"
    # Use 'f' format specifier to force standard decimal notation,
    # then combine with normalize() to remove trailing zeros.
    return f"{price_dec.normalize():f}"

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
    """
    A correct, event-driven weighted PnL calculation. It uses the event log
    as the single source of truth for all closing activities.
    """
    total_pnl_contribution = 0.0
    total_percent_closed = 0.0
    
    closure_event_types = ("PARTIAL_CLOSE_MANUAL", "PARTIAL_CLOSE_AUTO", "FINAL_PARTIAL_CLOSE")

    if not rec.events:
        if rec.status == RecommendationStatus.CLOSED and rec.exit_price is not None:
            return _pct(rec.entry.value, rec.exit_price, rec.side.value)
        return 0.0

    for event in rec.events:
        event_type = getattr(event, "event_type", "")
        if event_type in closure_event_types:
            data = getattr(event, "event_data", {}) or {}
            closed_pct = data.get('closed_percent', 0.0)
            pnl_on_part = data.get('pnl_on_part', 0.0)
            
            if closed_pct > 0:
                total_pnl_contribution += (closed_pct / 100.0) * pnl_on_part
                total_percent_closed += closed_pct

    # Fallback for simple trades closed by SL_HIT or MANUAL_CLOSE without partials
    if total_percent_closed == 0 and rec.status == RecommendationStatus.CLOSED and rec.exit_price is not None:
        return _pct(rec.entry.value, rec.exit_price, rec.side.value)
        
    # Normalize in case of floating point inaccuracies (e.g., 99.99% closed)
    if total_percent_closed > 99.9 and total_percent_closed < 100.1:
        normalization_factor = 100.0 / total_percent_closed
        return total_pnl_contribution * normalization_factor

    return total_pnl_contribution

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
    hit_targets = set()
    if rec.events:
        for event in rec.events:
            if event.event_type.startswith("TP") and event.event_type.endswith("_HIT"):
                try:
                    target_num = int(event.event_type[2:-4])
                    hit_targets.add(target_num)
                except (ValueError, IndexError):
                    continue
    next_tp_index = -1
    for i in range(1, len(rec.targets.values) + 1):
        if i not in hit_targets:
            next_tp_index = i
            break
    for i, target in enumerate(rec.targets.values, start=1):
        pct_value = _pct(entry_price, target.price.value, rec.side.value)
        if i in hit_targets: icon = "✅"
        elif i == next_tp_index: icon = "🚀"
        else: icon = "⏳"
        line = f"  • {icon} TP{i}: <code>{_format_price(target.price.value)}</code> ({_format_pnl(pct_value)})"
        if 0 < target.close_percent < 100:
            line += f" | Close {target.close_percent:.0f}%"
        lines.append(line)
    return "\n".join(lines)

def _build_logbook_section(rec: Recommendation) -> str:
    lines = []
    log_events = [
        event for event in (rec.events or []) 
        if getattr(event, "event_type", "") in ("PARTIAL_CLOSE_MANUAL", "PARTIAL_CLOSE_AUTO", "FINAL_PARTIAL_CLOSE")
    ]
    if not log_events:
        return ""
    lines.append("\n📋 <b>LOGBOOK</b>")
    for event in sorted(log_events, key=lambda ev: getattr(ev, "event_timestamp", datetime.min)):
        data = getattr(event, "event_data", {}) or {}
        pnl = data.get('pnl_on_part', 0.0)
        trigger = data.get('triggered_by', 'MANUAL')
        icon = "💰" if pnl >= 0 else "⚠️"
        lines.append(f"  • {icon} Closed {data.get('closed_percent', 0):.0f}% at <code>{_format_price(data.get('price', 0))}</code> ({_format_pnl(pnl)}) [{trigger}]")
    return "\n".join(lines)

def _build_summary_section(rec: Recommendation) -> str:
    pnl = _calculate_weighted_pnl(rec)
    return "\n".join([
        "📊 <b>TRADE SUMMARY</b>",
        f"💰 Entry: <code>{_format_price(rec.entry.value)}</code>",
        f"🏁 Final Exit Price: <code>{_format_price(rec.exit_price)}</code>",
        f"{'📈' if pnl >= 0 else '📉'} <b>Final Weighted Result: {_format_pnl(pnl)}</b> ({_get_result_text(pnl)})",
    ])

def build_trade_card_text(rec: Recommendation) -> str:
    header = _build_header(rec)
    parts = [header]
    
    if rec.status == RecommendationStatus.CLOSED:
        parts.append("─ ─ ─ ─ ─ ─ ─ ─ ─ ─")
        parts.append(_build_summary_section(rec))
        parts.append(_build_logbook_section(rec))
    else:
        if section := _build_live_price_section(rec): parts.append(section)
        parts.append(_build_performance_section(rec))
        parts.append(_build_exit_plan_section(rec))
        if section := _build_logbook_section(rec): parts.append(section)

    parts.append("────────────────────")
    parts.append(f"#{rec.asset.value} #Signal")
    if rec.notes: parts.append(f"📝 Notes: <i>{rec.notes}</i>")
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