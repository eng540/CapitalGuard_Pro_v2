# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE --
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any, Tuple
from math import isfinite
from datetime import datetime, timezone
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.domain.value_objects import Target

# --- Helper Functions ---

def _pct(entry: float, target_price: float, side: str) -> float:
    if not entry or entry == 0:
        return 0.0
    return ((target_price - entry) / entry * 100.0) if (side or "").upper() == "LONG" else ((entry - target_price) / entry * 100.0)

def _rr(entry: float, sl: float, first_target: Optional[Target], side: str) -> str:
    try:
        if first_target is None:
            return "—"
        risk = abs(entry - sl)
        if risk <= 0:
            return "—"
        reward = abs(first_target.price - entry)
        ratio = reward / risk
        return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception:
        return "—"

def _entry_scalar_and_zone(entry_val: Any) -> Tuple[float, Optional[Tuple[float, float]]]:
    if isinstance(entry_val, (list, tuple)) and entry_val:
        try:
            first, last = float(entry_val[0]), float(entry_val[-1])
            lo, hi = (first, last) if first <= last else (last, first)
            return first, (lo, hi)
        except Exception:
            try:
                return float(entry_val[0]), None
            except Exception:
                return 0.0, None
    try:
        return float(entry_val or 0), None
    except Exception:
        return 0.0, None

SEP = "━━━━━━━━━━━━━━"
SEP_LONG = "━━━━━━━━━━━━━━━━━━━━━━"

# --- Card Building Logic ---

def _build_pending_card(rec: Recommendation, live_price: Optional[float]) -> str:
    entry = rec.entry.value
    sl = rec.stop_loss.value
    tps = rec.targets.values
    tp1 = tps[0] if tps else None
    
    lines = [
        f"⏳ **PENDING | {rec.asset.value} | {rec.side.value}** {'🟢' if rec.side.value == 'LONG' else '🔴'}",
        f"Signal #{rec.id} | {rec.market} Market",
        SEP,
        "**The Plan:**",
        f"💰 **Entry:** {entry:g}",
        f"🛑 **Stop:** {sl:g}",
        "🎯 **Targets:**"
    ]
    
    for i, target in enumerate(tps, start=1):
        pct = _pct(entry, target.price, rec.side.value)
        line = f"  • TP{i}: {target.price:g} ({pct:+.2f}%)"
        if 0 < (target.close_percent or 0) < 100:
            line += f" (Close {target.close_percent}%)"
        lines.append(line)
        
    lines.extend([
        "",
        f"📈 **R/R (plan):** {_rr(entry, sl, tp1, rec.side.value)}",
        f"📝 **ملاحظات:** {rec.notes or '—'}",
        SEP,
        f"#{rec.asset.value} #Signal"
    ])
    return "\n".join(lines)

def _build_active_card(rec: Recommendation, live_price: Optional[float]) -> str:
    entry = rec.entry.value
    sl = rec.stop_loss.value
    tps = rec.targets.values

    header_icon = "📈"
    if live_price is not None:
        pnl_now = _pct(entry, live_price, rec.side.value)
        header_icon = "📈" if pnl_now >= 0 else "📉"
        pnl_text = f"PnL: {pnl_now:+.2f}%"
    else:
        pnl_text = ""

    lines = [
        f"{header_icon} **ACTIVE | {rec.asset.value} | {rec.side.value}** {'🟢' if rec.side.value == 'LONG' else '🔴'}",
        f"Signal #{rec.id} | {pnl_text}".rstrip(" | "),
        SEP_LONG
    ]

    # Independent Live Price block
    if live_price is not None:
        lines.extend([
            f"💹 **Live Price:** **{live_price:g}**",
            f"⏰ Updated: {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
            SEP_LONG
        ])
        
    # PERFORMANCE
    lines.append("**PERFORMANCE**")
    lines.append(f"💰 **Entry:** {entry:g}")
    stop_text = f"🛑 **Stop:** {sl:g}"
    if sl == entry:
        stop_text = f"🛡️ **Stop:** {sl:g} (Secured)"
    lines.append(stop_text)

    if getattr(rec, "profit_stop_price", None):
        lines.append(f"🔒 **Profit Stop:** {rec.profit_stop_price:g}")

    if getattr(rec, "open_size_percent", None) is not None:
        lines.append(f"📦 **Open Size:** {rec.open_size_percent:.2f}%")

    # EXIT PLAN
    lines.append("\n**EXIT PLAN**")
    lines.append("🎯 **Targets:**")

    alert_meta = getattr(rec, "alert_meta", {}) or {}
    hit_targets = alert_meta.get("hit_target_indices", []) if isinstance(alert_meta, dict) else []

    for i, target in enumerate(tps):
        icon = "✅" if i in hit_targets else ("🚀" if i == len(hit_targets) else "⏳")
        line = f"  • {icon} TP{i+1}: {target.price:g}"
        if (0 < (target.close_percent or 0) < 100) and (i not in hit_targets):
            line += f" (Close {target.close_percent}%)"
        lines.append(line)
        
    # LOGBOOK
    events = getattr(rec, "events", []) or []
    partial_profit_events = [e for e in events if "PARTIAL_PROFIT" in getattr(e, "event_type", "")]
    if partial_profit_events:
        lines.append("\n**LOGBOOK**")
        lines.append("💰 **Profits Taken:**")
        for event in partial_profit_events:
            data = getattr(event, "event_data", {}) or {}
            closed_pct = data.get("closed_percent", 0)
            price = data.get("price", 0.0)
            pnl_part = data.get("pnl_on_part", None)
            pnl_str = f"{pnl_part:+.2f}%" if pnl_part is not None else "—"
            lines.append(f"  • Closed {closed_pct}% at {price:g} ({pnl_str})")

    lines.extend([
        f"\n📝 **Notes:** {rec.notes or '—'}",
        SEP_LONG,
        f"#{rec.asset.value} #Signal"
    ])
    return "\n".join(lines)

def _build_closed_card(rec: Recommendation) -> str:
    entry = rec.entry.value
    sl = rec.stop_loss.value
    exit_price = rec.exit_price or 0.0
    pnl = _pct(entry, exit_price, rec.side.value)
    
    if pnl > 0.001:
        header_icon, result_text = "🏆", "WIN"
    elif pnl < -0.001:
        header_icon, result_text = "💔", "LOSS"
    else:
        header_icon, result_text = "🛡️", "BREAKEVEN"
        
    lines = [
        f"{header_icon} **CLOSED | {rec.asset.value} | {rec.side.value}** {'🟢' if rec.side.value == 'LONG' else '🔴'}",
        f"Signal #{rec.id} | {result_text}",
        SEP,
        "**Trade Summary:**",
        f"💰 **Entry:** {entry:g}",
        f"🏁 **Exit:** {exit_price:g}",
        f"{'📈' if pnl >= 0 else '📉'} **Final Result:** {pnl:+.2f}%",
        f"\n📝 **ملاحظات:** {rec.notes or '—'}",
        SEP,
        f"#{rec.asset.value} #Signal"
    ]
    return "\n".join(lines)

def build_trade_card_text(rec: Recommendation) -> str:
    live_price = getattr(rec, "live_price", None)
    if rec.status == RecommendationStatus.PENDING:
        return _build_pending_card(rec, live_price)
    elif rec.status == RecommendationStatus.ACTIVE:
        return _build_active_card(rec, live_price)
    elif rec.status == RecommendationStatus.CLOSED:
        return _build_closed_card(rec)
    return "Invalid recommendation state."

# --- Other builders ---
def _format_targets(entry_scalar: float, side: str, tps_for_display: List[Target]) -> str:
    parts = []
    for i, t in enumerate(tps_for_display, start=1):
        pct = _pct(entry_scalar, t.price, side)
        suffix = f" (Close {t.close_percent}%)" if 0 < (t.close_percent or 0) < 100 else ""
        parts.append(f"- TP{i}: {t.price:g} ({pct:+.2f}%){suffix}")
    return "\n".join(parts)

def build_review_text(draft: dict) -> str:
    asset = (draft.get("asset", "") or "").upper()
    side = (draft.get("side", "") or "").upper()
    market = (draft.get("market", "") or "-")
    entry_scalar, zone = _entry_scalar_and_zone(draft.get("entry"))
    sl = float(draft.get("stop_loss", 0) or 0)
    
    raw_tps = draft.get("targets", [])
    tps_for_display = [Target(price=t['price'], close_percent=t['close_percent']) for t in raw_tps]
    
    tp1 = tps_for_display[0] if tps_for_display else None
    planned_rr = _rr(entry_scalar, sl, tp1, side)
    notes = draft.get("notes") or "-"
    
    lines_tps = _format_targets(entry_scalar, side, tps_for_display)
    
    zone_line = f"\nEntry Zone: {zone[0]:g} — {zone[1]:g}" if zone else ""
    return (
        "📝 **مراجعة التوصية**\n\n"
        f"**{asset}** | {market} / {side}\n"
        f"Entry 💰: {entry_scalar:g}{zone_line}\n"
        f"SL 🛑: {sl:g}\n"
        f"Targets:\n{lines_tps}\n\n"
        f"R/R (plan): **{planned_rr}**\n"
        f"ملاحظات: _{notes}_\n\n"
        "هل تريد نشر هذه التوصية في القناة؟"
    )

def build_review_text_with_price(draft: dict, preview_price: float | None) -> str:
    base = build_review_text(draft)
    if preview_price is None:
        return base + "\n\n🔎 Current Price: —"
    try:
        return base + f"\n\n🔎 Current Price: **{float(preview_price):g}**"
    except Exception:
        return base + f"\n\n🔎 Current Price: **{preview_price}**"

def build_analyst_stats_text(stats: Dict[str, Any]) -> str:
    total = stats.get('total_recommendations', 0)
    open_recs = stats.get('open_recommendations', 0)
    closed_recs = stats.get('closed_recommendations', 0)
    win_rate = stats.get('overall_win_rate', '0.00%')
    total_pnl = stats.get('total_pnl_percent', '0.00%')
    lines = [
        "📊 **Your Performance Summary** 📊",
        "-" * 15,
        f"Total Recommendations: **{total}**",
        f"Open Trades: **{open_recs}**",
        f"Closed Trades: **{closed_recs}**",
        "-" * 15,
        f"Overall Win Rate: **{win_rate}**",
        f"Total PnL (Cumulative %): **{total_pnl}**",
        "-" * 15,
        f"_Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
    ]
    return "\n".join(lines)
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---