# --- START OF FINAL, COMPLETE, AND VISUALLY-ENHANCED FILE (Version 12.3.0) ---
# src/capitalguard/interfaces/telegram/ui_texts.py

from __future__ import annotations
from typing import List, Optional, Dict, Any, Tuple
from math import isfinite
from datetime import datetime, timezone
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.domain.value_objects import Target

# --- Helper Functions ---

def _pct(entry: float, target_price: float, side: str) -> float:
    """Calculates the percentage difference for a trade."""
    if not entry or entry == 0:
        return 0.0
    return ((target_price - entry) / entry * 100.0) if (side or "").upper() == "LONG" else ((entry - target_price) / entry * 100.0)

def _rr(entry: float, sl: float, first_target: Optional[Target], side: str) -> str:
    """Calculates the Risk/Reward ratio based on the first target."""
    try:
        if first_target is None: return "—"
        risk = abs(entry - sl)
        if risk <= 0: return "—"
        reward = abs(first_target.price - entry)
        ratio = reward / risk
        return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception:
        return "—"

# --- Card Building Logic (Completely Rebuilt for Clarity and Professional Look) ---

def _build_header(rec: Recommendation, status_icon: str, status_text: str) -> List[str]:
    """Builds the standardized header for all card types using HTML formatting."""
    side_icon = "🟢" if rec.side.value == "LONG" else "🔴"
    return [
        f"{status_icon} <b>{status_text} | {rec.asset.value} | {rec.side.value}</b> {side_icon}",
        f"Signal #{rec.id}",
    ]

def _build_plan_section(rec: Recommendation) -> List[str]:
    """Builds the 'PLAN' section of the card with clear formatting."""
    lines = [
        "─" * 20,
        "🎯 <b>THE PLAN</b>",
        f"💰 Entry: <code>{rec.entry.value:g}</code>",
        f"🛑 Stop: <code>{rec.stop_loss.value:g}</code>",
        "📈 Targets:",
    ]
    for i, target in enumerate(rec.targets.values, start=1):
        pct = _pct(rec.entry.value, target.price, rec.side.value)
        close_info = f" (Close {target.close_percent:.1f}%)" if 0 < target.close_percent < 100 else ""
        lines.append(f"  • TP{i}: <code>{target.price:g}</code> ({pct:+.2f}%){close_info}")
    
    tp1 = rec.targets.values[0] if rec.targets.values else None
    lines.append(f"📊 Risk/Reward: ~<code>{_rr(rec.entry.value, rec.stop_loss.value, tp1, rec.side.value)}</code>")
    return lines

def _build_performance_section(rec: Recommendation, live_price: Optional[float]) -> List[str]:
    """Builds the 'PERFORMANCE' section for active cards."""
    lines = ["─" * 20, "📈 <b>PERFORMANCE</b>"]
    
    if live_price is not None:
        pnl = _pct(rec.entry.value, live_price, rec.side.value)
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"💹 Live Price: <code>{live_price:g}</code> ({pnl_icon} {pnl:+.2f}%)")
    
    lines.append(f"💰 Entry: <code>{rec.entry.value:g}</code>")
    
    stop_text = f"🛑 Stop: <code>{rec.stop_loss.value:g}</code>"
    if rec.stop_loss.value == rec.entry.value:
        stop_text += " (at Breakeven)"
    lines.append(stop_text)

    if rec.profit_stop_price is not None:
        lines.append(f"🔒 Profit Stop: <code>{rec.profit_stop_price:g}</code>")
    
    lines.append(f"📦 Open Size: <code>{rec.open_size_percent:.2f}%</code>")
    return lines

def _build_logbook_section(rec: Recommendation) -> List[str]:
    """Builds the 'LOGBOOK' section if there are partial profit events."""
    events = getattr(rec, "events", []) or []
    partial_profit_events = [e for e in events if "PARTIAL_PROFIT" in getattr(e, "event_type", "")]
    if not partial_profit_events:
        return []

    lines = ["─" * 20, "📋 <b>LOGBOOK</b>", "💰 Profits Taken:"]
    for event in partial_profit_events:
        data = getattr(event, "event_data", {}) or {}
        closed_pct = data.get("closed_percent", 0)
        price = data.get("price", 0.0)
        pnl_part = data.get("pnl_on_part", 0.0)
        trigger = "Auto" if data.get("triggered_by") == "AUTO" else "Manual"
        lines.append(f"  • Closed {closed_pct:.1f}% at <code>{price:g}</code> ({pnl_part:+.2f}%) [{trigger}]")
    return lines

def _build_footer(rec: Recommendation) -> List[str]:
    """Builds the standardized footer."""
    notes = f"\n📝 <b>Notes:</b> <i>{rec.notes}</i>" if rec.notes else ""
    return [
        "─" * 20,
        f"#{rec.asset.value} #Signal{notes}"
    ]

def _build_pending_card(rec: Recommendation) -> str:
    lines = _build_header(rec, "⏳", "PENDING")
    lines.extend(_build_plan_section(rec))
    lines.extend(_build_footer(rec))
    return "\n".join(lines)

def _build_active_card(rec: Recommendation, live_price: Optional[float]) -> str:
    lines = _build_header(rec, "⚡️", "ACTIVE")
    lines.extend(_build_performance_section(rec, live_price))
    lines.extend(_build_logbook_section(rec))
    lines.extend(_build_footer(rec))
    return "\n".join(lines)

def _build_closed_card(rec: Recommendation) -> str:
    pnl = _pct(rec.entry.value, rec.exit_price or 0.0, rec.side.value)
    if pnl > 0.001:
        header_icon, result_text = "🏆", "WIN"
    elif pnl < -0.001:
        header_icon, result_text = "💔", "LOSS"
    else:
        header_icon, result_text = "🛡️", "BREAKEVEN"
        
    lines = _build_header(rec, header_icon, "CLOSED")
    lines.extend([
        "─" * 20,
        "🏁 <b>TRADE SUMMARY</b>",
        f"💰 Entry: <code>{rec.entry.value:g}</code>",
        f"🚪 Exit: <code>{rec.exit_price:g}</code>",
        f"<b>Final Result: {pnl:+.2f}% ({result_text})</b>"
    ])
    lines.extend(_build_footer(rec))
    return "\n".join(lines)

def build_trade_card_text(rec: Recommendation) -> str:
    """
    The main function to build the text for a recommendation card.
    It now delegates to specialized functions for each status, ensuring a clean and consistent look.
    """
    live_price = getattr(rec, "live_price", None)
    if rec.status == RecommendationStatus.PENDING:
        return _build_pending_card(rec)
    elif rec.status == RecommendationStatus.ACTIVE:
        return _build_active_card(rec, live_price)
    elif rec.status == RecommendationStatus.CLOSED:
        return _build_closed_card(rec)
    return "Invalid recommendation state."

# --- Other builders (for conversation handlers) ---

def build_review_text_with_price(draft: dict, preview_price: float | None) -> str:
    """Builds the review card text for the analyst before publishing."""
    asset = (draft.get("asset", "") or "").upper()
    side = (draft.get("side", "") or "").upper()
    market = (draft.get("market", "") or "-")
    entry = float(draft.get("entry", 0) or 0)
    sl = float(draft.get("stop_loss", 0) or 0)
    
    raw_tps = draft.get("targets", [])
    tps = [Target(price=t['price'], close_percent=t['close_percent']) for t in raw_tps]
    
    tp1 = tps[0] if tps else None
    planned_rr = _rr(entry, sl, tp1, side)
    notes = draft.get("notes") or "—"
    
    target_lines = []
    for i, t in enumerate(tps, start=1):
        pct = _pct(entry, t.price, side)
        close_info = f" (Close {t.close_percent:.1f}%)" if 0 < t.close_percent < 100 else ""
        target_lines.append(f"  • TP{i}: <code>{t.price:g}</code> ({pct:+.2f}%){close_info}")

    price_line = f"🔎 Current Price: <b>{preview_price:g}</b>" if preview_price is not None else "🔎 Current Price: —"

    return (
        f"📝 <b>REVIEW RECOMMENDATION</b>\n"
        f"─" * 20 + "\n"
        f"<b>{asset}</b> | {market} / {side}\n\n"
        f"💰 Entry: <code>{entry:g}</code>\n"
        f"🛑 Stop: <code>{sl:g}</code>\n"
        f"📈 Targets:\n" + "\n".join(target_lines) + "\n\n"
        f"📊 R/R (plan): <b>{planned_rr}</b>\n"
        f"📝 Notes: <i>{notes}</i>\n"
        f"─" * 20 + "\n"
        f"{price_line}\n\n"
        "Ready to publish?"
    )

def build_analyst_stats_text(stats: Dict[str, Any]) -> str:
    total = stats.get('total_recommendations', 0)
    open_recs = stats.get('open_recommendations', 0)
    closed_recs = stats.get('closed_recommendations', 0)
    win_rate = stats.get('overall_win_rate', '0.00%')
    total_pnl = stats.get('total_pnl_percent', '0.00%')
    
    lines = [
        "📊 <b>Your Performance Summary</b> 📊",
        "─" * 20,
        f"Total Recommendations: <b>{total}</b>",
        f"Open Trades: <b>{open_recs}</b>",
        f"Closed Trades: <b>{closed_recs}</b>",
        "─" * 20,
        f"Win Rate: <b>{win_rate}</b>",
        f"Total PnL (Cumulative): <b>{total_pnl}</b>",
        "─" * 20,
        f"<i>Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>",
    ]
    return "\n".join(lines)

# --- END OF FINAL, COMPLETE, AND VISUALLY-ENHANCED FILE ---