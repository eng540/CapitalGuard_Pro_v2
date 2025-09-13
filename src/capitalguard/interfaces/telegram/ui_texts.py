# --- START OF FINAL, CORRECTED, AND READY-TO-USE FILE ---
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any, Tuple
from math import isfinite
from datetime import datetime, timezone
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.domain.value_objects import Target # Import Target

# --- Helper Functions (Updated to handle Target objects) ---

def _pct(entry: float, target_price: float, side: str) -> float:
    if not entry or entry == 0: return 0.0
    return ((target_price - entry) / entry * 100.0) if (side or "").upper() == "LONG" else ((entry - target_price) / entry * 100.0)

def _rr(entry: float, sl: float, first_target: Optional[Target], side: str) -> str:
    try:
        if first_target is None: return "—"
        risk = abs(entry - sl)
        if risk <= 0: return "—"
        reward = abs(first_target.price - entry)
        ratio = reward / risk
        return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception: return "—"

def _rr_actual(entry: float, sl: float, exit_price: Optional[float], side: str) -> str:
    try:
        if exit_price is None: return "—"
        risk = abs(entry - sl)
        if risk <= 0: return "—"
        reward = abs(exit_price - entry)
        ratio = reward / risk
        return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception: return "—"

def _format_targets(entry: float, side: str, targets: List[Target]) -> str:
    lines: List[str] = []
    for i, target in enumerate(targets, start=1):
        try:
            pct = _pct(entry, target.price, side)
            line = f"• TP{i}: {target.price:g} ({pct:+.2f}%)"
            if target.close_percent > 0:
                line += f" <i>(إغلاق {target.close_percent}%)</i>"
            lines.append(line)
        except (ValueError, TypeError): continue
    return "\n".join(lines) if lines else "—"

def _format_targets_for_active_trade(entry: float, side: str, targets: List[Target], live_price: float) -> str:
    lines: List[str] = []
    for i, target in enumerate(targets, start=1):
        try:
            pct = _pct(entry, target.price, side)
            total_dist = abs(target.price - entry)
            current_dist = abs(live_price - entry)
            progress = 0
            if side.upper() == "LONG":
                progress = min(100, (live_price - entry) / total_dist * 100) if total_dist > 0 else (100 if live_price >= target.price else 0)
            else: # SHORT
                progress = min(100, (entry - live_price) / total_dist * 100) if total_dist > 0 else (100 if live_price <= target.price else 0)
            
            progress = max(0, progress)
            blocks = int(progress / 10)
            progress_bar = '█' * blocks + '─' * (10 - blocks)
            
            line = f"• TP{i}: {target.price:g} ({pct:+.2f}%) - <i>[{progress_bar}] {progress:.0f}%</i>"
            lines.append(line)
        except (ValueError, TypeError): continue
    return "\n".join(lines) if lines else "—"

def _entry_scalar_and_zone(entry_val: Any) -> Tuple[float, Optional[Tuple[float, float]]]:
    # This function remains the same as it handles raw input
    if isinstance(entry_val, (list, tuple)) and entry_val:
        try:
            first, last = float(entry_val[0]), float(entry_val[-1])
            lo, hi = (first, last) if first <= last else (last, first)
            return first, (lo, hi)
        except Exception:
            try: return float(entry_val[0]), None
            except Exception: return 0.0, None
    try: return float(entry_val or 0), None
    except Exception: return 0.0, None

# --- Main Card Builder (Corrected for Target objects) ---
def build_trade_card_text(rec: Recommendation) -> str:
    rec_id = getattr(rec, "id", None)
    asset = getattr(getattr(rec, "asset", None), "value", "N/A")
    side = getattr(getattr(rec, "side", None), "value", "N/A")
    entry = float(getattr(getattr(rec, "entry", None), "value", 0))
    sl = float(getattr(getattr(rec, "stop_loss", None), "value", 0))
    targets_obj = getattr(rec, "targets", None)
    tps = getattr(targets_obj, "values", []) # This is now a List[Target]
    status = getattr(rec, "status", RecommendationStatus.PENDING)
    live_price = getattr(rec, "live_price", None)
    open_size = getattr(rec, "open_size_percent", 100.0)
    now_utc = datetime.now(timezone.utc).strftime('%H:%M %Z')

    title_line = f"<b>{asset}</b> — {side}"
    if rec_id:
        title_line = f"Signal #{rec_id} | <b>{asset}</b> — {side}"

    body_lines: List[str] = []
    targets_text: str = ""

    if status == RecommendationStatus.PENDING:
        body_lines.append("Status: ⏳ <b>PENDING ENTRY</b>")
        if live_price and isfinite(live_price):
            dist_pct = _pct(entry, float(live_price), "LONG") # Here it's just for distance, side doesn't matter
            body_lines.append(f"<i>Live Price ({now_utc}): {float(live_price):g}</i>")
            body_lines.append(f"<i>Distance to Entry: {abs(dist_pct):.2f}%</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        targets_text = "<u>Targets (Plan)</u>:\n" + _format_targets(entry, side, tps)

    elif status == RecommendationStatus.ACTIVE:
        body_lines.append(f"Status: 🟢 <b>ACTIVE</b> (الحجم المفتوح: {open_size:.2f}%)")
        if live_price and isfinite(live_price):
            pnl = _pct(entry, float(live_price), side)
            body_lines.append(f"<i>Live Price ({now_utc}): {float(live_price):g} (PnL: {pnl:+.2f}%)</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        
        targets_text = "<u>Targets (Live Progress)</u>:\n"
        if live_price and isfinite(live_price):
            targets_text += _format_targets_for_active_trade(entry, side, tps, float(live_price))
        else:
            targets_text += _format_targets(entry, side, tps)

    elif status == RecommendationStatus.CLOSED:
        exit_p = getattr(rec, 'exit_price', None)
        pnl = _pct(entry, float(exit_p), side) if exit_p is not None and isfinite(exit_p) else 0.0
        rr_act = _rr_actual(entry, sl, float(exit_p or 0), side)
        body_lines.append(f"Status: ✅ <b>CLOSED</b> at {float(exit_p):g}" if exit_p is not None else "Status: ✅ <b>CLOSED</b>")
        result_line = f"Result: <b>Profit of {pnl:+.2f}%</b>" if pnl >= 0 else f"Result: <b>Loss of {pnl:+.2f}%</b>"
        body_lines.append(f"{result_line} (R/R act: {rr_act})")
        targets_text = ""

    notes_text = f"\nNotes: <i>{rec.notes or '—'}</i>"
    footer_lines = [f"#{asset} #Signal #{side}"]

    final_parts = [title_line] + body_lines
    if targets_text:
        final_parts.append(targets_text)
    final_parts.extend([notes_text] + footer_lines)
    
    return "\n".join(final_parts)

# --- Other builders (Corrected for Target objects) ---
def build_review_text(draft: dict) -> str:
    asset = (draft.get("asset", "") or "").upper()
    side = (draft.get("side", "") or "").upper()
    market = (draft.get("market", "") or "-")
    entry_scalar, zone = _entry_scalar_and_zone(draft.get("entry"))
    sl = float(draft.get("stop_loss", 0) or 0)
    
    # This part needs to handle raw input which is just floats
    raw_tps = draft.get("targets", [])
    tps_for_display = [Target(price=p, close_percent=0) for p in raw_tps]
    
    tp1 = tps_for_display[0] if tps_for_display else None
    planned_rr = _rr(entry_scalar, sl, tp1, side)
    notes = draft.get("notes") or "-"
    lines_tps = "\n".join([f"• TP{i}: {tp.price:g}" for i, tp in enumerate(tps_for_display, start=1)]) or "—"
    zone_line = f"\nEntry Zone: {zone[0]:g} — {zone[1]:g}" if zone else ""
    return (
        "📝 <b>مراجعة التوصية</b>\n\n"
        f"<b>{asset}</b> | {market} / {side}\n"
        f"Entry 💰: {entry_scalar:g}{zone_line}\n"
        f"SL 🛑: {sl:g}\n"
        f"<u>Targets</u>:\n{lines_tps}\n\n"
        f"R/R (plan): <b>{planned_rr}</b>\n"
        f"ملاحظات: <i>{notes}</i>\n\n"
        "هل تريد نشر هذه التوصية في القناة؟"
    )

def build_review_text_with_price(draft: dict, preview_price: float | None) -> str:
    base = build_review_text(draft)
    if preview_price is None: return base + "\n\n🔎 Current Price: —"
    try: return base + f"\n\n🔎 Current Price: <b>{float(preview_price):g}</b>"
    except Exception: return base + f"\n\n🔎 Current Price: <b>{preview_price}</b>"

def build_analyst_stats_text(stats: Dict[str, Any]) -> str:
    total = stats.get('total_recommendations', 0); open_recs = stats.get('open_recommendations', 0)
    closed_recs = stats.get('closed_recommendations', 0); win_rate = stats.get('overall_win_rate', '0.00%')
    total_pnl = stats.get('total_pnl_percent', '0.00%')
    lines = [
        "📊 <b>Your Performance Summary</b> 📊", "─" * 15, f"Total Recommendations: <b>{total}</b>",
        f"Open Trades: <b>{open_recs}</b>", f"Closed Trades: <b>{closed_recs}</b>", "─" * 15,
        f"Overall Win Rate: <b>{win_rate}</b>", f"Total PnL (Cumulative %): <b>{total_pnl}</b>", "─" * 15,
        f"<i>Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>",
    ]
    return "\n".join(lines)
# --- END OF FINAL, CORRECTED, AND READY-TO-USE FILE ---