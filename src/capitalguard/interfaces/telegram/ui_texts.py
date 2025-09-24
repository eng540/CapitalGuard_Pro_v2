# --- START OF FINAL, HARDENED, AND PRODUCTION-READY FILE (Version 16.2.1) ---
# src/capitalguard/interfaces/telegram/ui_texts.py

from __future__ import annotations
from typing import List, Optional, Dict, Any, Tuple
from math import isfinite
from datetime import datetime, timezone
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.domain.value_objects import Target

# --- Helper Functions ---

def _pct(entry: float, target_price: float, side: str) -> float:
    if not entry or entry == 0 or not isfinite(entry) or not isfinite(target_price):
        return 0.0
    side_upper = (side or "").upper()
    if side_upper == "LONG":
        return ((target_price / entry) - 1) * 100.0
    elif side_upper == "SHORT":
        return ((entry / target_price) - 1) * 100.0
    return 0.0

def _rr(entry: float, sl: float, first_target: Optional[Target]) -> str:
    try:
        if first_target is None or not isfinite(entry) or not isfinite(sl):
            return "—"
        risk = abs(entry - sl)
        if risk <= 1e-9:
            return "∞"
        reward = abs(first_target.price - entry)
        ratio = reward / risk
        return f"1:{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception:
        return "—"

def _calculate_weighted_pnl(rec: Recommendation) -> float:
    total_pnl = 0.0
    percent_closed = 0.0
    
    for event in (rec.events or []):
        event_type = getattr(event, "event_type", "")
        if event_type in ("PARTIAL_PROFIT_MANUAL", "PARTIAL_PROFIT_AUTO"):
            data = getattr(event, "event_data", {}) or {}
            closed_pct = data.get('closed_percent', 0.0)
            pnl_on_part = data.get('pnl_on_part', 0.0)
            
            if closed_pct > 0:
                total_pnl += (closed_pct / 100.0) * pnl_on_part
                percent_closed += closed_pct

    remaining_percent = 100.0 - percent_closed
    if remaining_percent > 0.01 and rec.exit_price is not None:
        pnl_on_remaining = _pct(rec.entry.value, rec.exit_price, rec.side.value)
        total_pnl += (remaining_percent / 100.0) * pnl_on_remaining
    elif abs(remaining_percent) < 0.01 and total_pnl != 0:
        pass
    elif percent_closed == 0 and rec.exit_price is not None:
        return _pct(rec.entry.value, rec.exit_price, rec.side.value)

    return total_pnl

# --- Card Building Blocks ---

def _build_header(rec: Recommendation) -> str:
    status_map = {
        RecommendationStatus.PENDING: "⏳ PENDING",
        RecommendationStatus.ACTIVE: "⚡️ ACTIVE",
        RecommendationStatus.CLOSED: "🏁 CLOSED",
    }
    status_text = status_map.get(rec.status, "UNKNOWN")
    side_icon = '🟢' if getattr(rec.side, "value", "").upper() == 'LONG' else '🔴'
    return f"<b>{status_text} | #{rec.asset.value} | {rec.side.value}</b> {side_icon} | Signal #{rec.id}"

def _build_live_price_section(rec: Recommendation, live_price: Optional[float]) -> str:
    if rec.status not in (RecommendationStatus.ACTIVE, RecommendationStatus.PENDING) or live_price is None:
        return ""
    lines = ["─ ─ ─ ─ ─ ─ ─ ─ ─ ─"]
    if rec.status == RecommendationStatus.ACTIVE:
        pnl = _pct(rec.entry.value, live_price, rec.side.value)
        pnl_icon = '🟢' if pnl >= 0 else '🔴'
        lines.append(f"💹 <b>Live Price:</b> <code>{live_price:g}</code> ({pnl_icon} {pnl:+.2f}%)")
    elif rec.status == RecommendationStatus.PENDING:
        distance = abs(live_price - rec.entry.value)
        distance_pct = (distance / rec.entry.value) * 100.0 if rec.entry.value > 0 else 0.0
        lines.append(f"💹 <b>Live Price:</b> <code>{live_price:g}</code> (~{distance_pct:.2f}% from entry)")
    lines.append("─ ─ ─ ─ ─ ─ ─ ─ ─ ─")
    return "\n".join(lines)

def _build_performance_section(rec: Recommendation) -> str:
    lines = ["📊 <b>PERFORMANCE</b>"]
    entry_price = rec.entry.value
    stop_loss = rec.stop_loss.value
    lines.append(f"💰 Entry: <code>{entry_price:g}</code>")
    sl_pnl = _pct(entry_price, stop_loss, rec.side.value)
    lines.append(f"🛑 Stop: <code>{stop_loss:g}</code> ({sl_pnl:+.2f}%)")
    if getattr(rec, "profit_stop_price", None) is not None:
        if rec.profit_stop_price == entry_price:
            lines.append(f"🔒 <b>Break-Even:</b> <code>{rec.profit_stop_price:g}</code> (Secured ✅)")
        else:
            lines.append(f"🔒 <b>Profit Stop:</b> <code>{rec.profit_stop_price:g}</code>")
    first_target = rec.targets.values[0] if getattr(rec.targets, "values", []) else None
    lines.append(f"💡 Risk/Reward (Plan): ~<code>{_rr(entry_price, stop_loss, first_target)}</code>")
    return "\n".join(lines)

def _build_exit_plan_section(rec: Recommendation) -> str:
    lines = ["\n🎯 <b>EXIT PLAN</b>"]
    entry_price = rec.entry.value
    events = rec.events or []
    hit_targets_events = set()
    for event in events:
        event_type = getattr(event, "event_type", "")
        if event_type.startswith("TP") and event_type.endswith("_HIT"):
            try:
                idx = int(event_type[2:-4])
                hit_targets_events.add(idx)
            except (ValueError, IndexError):
                continue
    targets_list = getattr(rec.targets, "values", [])
    if not targets_list:
        return ""
    next_tp_index = -1
    for i in range(1, len(targets_list) + 1):
        if i not in hit_targets_events:
            next_tp_index = i
            break
    for i, target in enumerate(targets_list, start=1):
        pct = _pct(entry_price, target.price, rec.side.value)
        if i in hit_targets_events:
            icon = "✅"
        elif i == next_tp_index:
            icon = "🚀"
        else:
            icon = "⏳"
        line = f"  • {icon} TP{i}: <code>{target.price:g}</code> ({pct:+.2f}%)"
        if 0 < getattr(target, "close_percent", 0) < 100:
            line += f" | Close {target.close_percent:.0f}%"
        lines.append(line)
    return "\n".join(lines)

def _build_logbook_section(rec: Recommendation) -> str:
    lines = []
    events = rec.events or []
    log_events = [event for event in events if getattr(event, "event_type", "") in ("PARTIAL_PROFIT_MANUAL", "PARTIAL_PROFIT_AUTO", "SL_UPDATED")]
    if not log_events:
        return ""
    lines.append("\n📋 <b>LOGBOOK</b>")
    for event in sorted(log_events, key=lambda ev: getattr(ev, "event_timestamp", datetime.min)):
        et = getattr(event, "event_type", "")
        data = getattr(event, "event_data", {}) or {}
        if et in ("PARTIAL_PROFIT_MANUAL", "PARTIAL_PROFIT_AUTO"):
            pnl = data.get('pnl_on_part', 0.0)
            trigger = "Manual" if et == "PARTIAL_PROFIT_MANUAL" else "Auto"
            lines.append(f"  • 💰 Closed {data.get('closed_percent', 0):.0f}% at <code>{data.get('price', 0):g}</code> ({pnl:+.2f}%) [{trigger}]")
        elif et == "SL_UPDATED" and data.get('new_sl') == rec.entry.value:
             lines.append(f"  • 🛡️ SL moved to Breakeven.")
    return "\n".join(lines)

def _build_summary_section(rec: Recommendation) -> str:
    entry = rec.entry.value
    exit_price = getattr(rec, "exit_price", 0.0) or 0.0
    pnl = _calculate_weighted_pnl(rec)
    if pnl > 0.001: result_text = "🏆 WIN"
    elif pnl < -0.001: result_text = "💔 LOSS"
    else: result_text = "🛡️ BREAKEVEN"
    lines = [
        "📊 <b>TRADE SUMMARY</b>",
        f"💰 Entry: <code>{entry:g}</code>",
        f"🏁 Exit: <code>{exit_price:g}</code>",
        f"{'📈' if pnl >= 0 else '📉'} <b>Final Result: {pnl:+.2f}%</b> ({result_text})",
    ]
    return "\n".join(lines)

def _build_pending_card(rec: Recommendation, live_price: Optional[float]) -> str:
    parts = [
        _build_header(rec),
        _build_live_price_section(rec, live_price),
        _build_performance_section(rec),
        _build_exit_plan_section(rec),
        f"\n📝 <b>Notes:</b> <i>{rec.notes or '—'}</i>"
    ]
    return "\n".join(filter(None, parts))

def _build_active_card(rec: Recommendation, live_price: Optional[float]) -> str:
    parts = [
        _build_header(rec),
        _build_live_price_section(rec, live_price),
        _build_performance_section(rec),
        _build_exit_plan_section(rec),
        _build_logbook_section(rec),
        "────────────────────",
        f"#{rec.asset.value} #Signal",
        f"📝 <b>Notes:</b> <i>{rec.notes or '—'}</i>"
    ]
    return "\n".join(filter(None, parts))

def _build_closed_card(rec: Recommendation) -> str:
    parts = [
        _build_header(rec),
        "─ ─ ─ ─ ─ ─ ─ ─ ─ ─",
        _build_summary_section(rec),
        _build_logbook_section(rec),
        "────────────────────",
        f"#{rec.asset.value} #Signal",
        f"📝 <b>Notes:</b> <i>{rec.notes or '—'}</i>"
    ]
    return "\n".join(filter(None, parts))

def build_trade_card_text(rec: Recommendation) -> str:
    """The main function to generate the text for any recommendation card."""
    live_price = getattr(rec, "live_price", None)
    if rec.status == RecommendationStatus.PENDING:
        return _build_pending_card(rec, live_price)
    if rec.status == RecommendationStatus.ACTIVE:
        return _build_active_card(rec, live_price)
    if rec.status == RecommendationStatus.CLOSED:
        return _build_closed_card(rec)
    return "Invalid recommendation state."

def build_review_text_with_price(draft: dict, preview_price: Optional[float]) -> str:
    asset = (draft.get("asset", "") or "").upper()
    side = (draft.get("side", "") or "").upper()
    market = (draft.get("market", "") or "-")
    entry = float(draft.get("entry", 0) or 0)
    sl = float(draft.get("stop_loss", 0) or 0)
    raw_tps = draft.get("targets", [])
    tps = [Target(price=t['price'], close_percent=t.get('close_percent', 0)) for t in raw_tps]
    tp1 = tps[0] if tps else None
    planned_rr = _rr(entry, sl, tp1)
    notes = draft.get("notes") or "—"
    target_lines = []
    for i, t in enumerate(tps, start=1):
        pct = _pct(entry, t.price, side)
        suffix = f" (Close {t.close_percent:.0f}%)" if 0 < t.close_percent < 100 else ""
        target_lines.append(f"  • TP{i}: <code>{t.price:g}</code> ({pct:+.2f}%){suffix}")
    base_text = (
        f"📝 <b>REVIEW RECOMMENDATION</b>\n"
        f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        f"<b>{asset} | {market} / {side}</b>\n\n"
        f"💰 Entry: <code>{entry:g}</code>\n"
        f"🛑 Stop: <code>{sl:g}</code>\n"
        f"🎯 Targets:\n" + "\n".join(target_lines) + "\n\n"
        f"💡 R/R (plan): ~<code>{planned_rr}</code>\n"
        f"📝 Notes: <i>{notes}</i>\n"
        f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─"
    )
    if preview_price is not None:
        base_text += f"\n💹 Current Price: <code>{preview_price:g}</code>"
    base_text += "\n\nReady to publish?"
    return base_text

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
        f"Overall Win Rate: <b>{win_rate}</b>",
        f"Total PnL (Cumulative %): <b>{total_pnl}</b>",
        "─" * 20,
        f"<i>Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>",
    ]
    return "\n".join(lines)

# --- END OF FINAL, HARDENED, AND PRODUCTION-READY FILE (Version 16.2.1) ---