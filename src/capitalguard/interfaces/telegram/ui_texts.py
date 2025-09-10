# --- START OF FINAL, COMPLETE, AND MERGED FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any, Tuple
from math import isfinite
from datetime import datetime, timezone
from capitalguard.domain.entities import Recommendation, RecommendationStatus

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# Helpers (Taken directly from your original, correct file)
# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
def _pct(entry: float, target: float, side: str) -> float:
    if not entry or entry == 0: return 0.0
    return ((target - entry) / entry * 100.0) if (side or "").upper() == "LONG" else ((entry - target) / entry * 100.0)

def _rr(entry: float, sl: float, tp1: Optional[float], side: str) -> str:
    try:
        risk = abs(entry - sl)
        if risk <= 0 or tp1 is None: return "—"
        reward = abs(tp1 - entry)
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

def _format_targets(entry: float, side: str, tps: Iterable[float]) -> str:
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        try:
            tp_f = float(tp)
            pct = _pct(entry, tp_f, side)
            lines.append(f"• TP{i}: {tp_f:g} ({pct:+.2f}%)")
        except (ValueError, TypeError): continue
    return "\n".join(lines) if lines else "—"

def _format_targets_for_active_trade(entry: float, side: str, tps: List[float], live_price: float) -> str:
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        try:
            tp_f = float(tp)
            pct = _pct(entry, tp_f, side)
            total_dist = abs(tp_f - entry)
            current_dist = abs(live_price - entry)
            progress = min(100, (current_dist / total_dist) * 100) if total_dist > 0 else (100 if live_price >= tp_f else 0)
            if side.upper() == "SHORT":
                progress = min(100, (entry - live_price) / (entry - tp_f) * 100) if (entry - tp_f) != 0 else (100 if live_price <= tp_f else 0)
            progress = max(0, progress)
            blocks = int(progress / 10)
            progress_bar = '█' * blocks + '─' * (10 - blocks)
            lines.append(f"• TP{i}: {tp_f:g} ({pct:+.2f}%) - <i>[{progress_bar}] {progress:.0f}%</i>")
        except (ValueError, TypeError): continue
    return "\n".join(lines) if lines else "—"

def _entry_scalar_and_zone(entry_val: Any) -> Tuple[float, Optional[Tuple[float, float]]]:
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

# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# Main Card Builder (MERGED and IMPROVED)
# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
def build_trade_card_text(rec) -> str:
    """
    Builds the complete text for a recommendation card, preserving the original
    logic and adding the new structured notes parsing.
    """
    rec_id = getattr(rec, "id", None)
    asset = getattr(getattr(rec, "asset", None), "value", "N/A")
    side = getattr(getattr(rec, "side", None), "value", "N/A")
    entry = float(getattr(getattr(rec, "entry", None), "value", 0))
    sl = float(getattr(getattr(rec, "stop_loss", None), "value", 0))
    tps = list(getattr(getattr(rec, "targets", None), "values", []))
    status = getattr(rec, "status", RecommendationStatus.PENDING)
    live_price = getattr(rec, "live_price", None)
    now_utc = datetime.now(timezone.utc).strftime('%H:%M %Z')

    title_line = f"<b>{asset}</b> — {side}"
    if rec_id:
        title_line = f"Signal #{rec_id} | <b>{asset}</b> — {side}"

    body_lines: List[str] = []
    targets_text: str = ""

    if status == RecommendationStatus.PENDING:
        body_lines.append("Status: ⏳ <b>PENDING ENTRY</b>")
        if live_price and isfinite(live_price):
            dist_pct = _pct(entry, float(live_price), "LONG")
            body_lines.append(f"<i>Live Price ({now_utc}): {float(live_price):g}</i>")
            body_lines.append(f"<i>Distance to Entry: {dist_pct:+.2f}%</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        targets_text = "<u>Targets (Plan)</u>:\n" + _format_targets(entry, side, tps)

    elif status == RecommendationStatus.ACTIVE:
        body_lines.append("Status: 🟢 <b>ACTIVE</b>")
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

    # ✅ --- NEW Structured Notes Parsing ---
    manual_notes = []
    auto_notes = []
    notes_text = ""
    if rec.notes:
        for line in rec.notes.strip().split('\n'):
            line = line.strip()
            if not line: continue
            if line.startswith("[SL_UPDATE]:"): auto_notes.append(f"- تم تحديث الوقف إلى {line.split(':', 1)[1]}")
            elif line.startswith("[TP_UPDATE]:"): auto_notes.append(f"- تم تحديث الأهداف إلى [{line.split(':', 1)[1]}]")
            elif line.startswith("[PARTIAL_CLOSE]:"): auto_notes.append(f"- تم إغلاق 50% من الصفقة في {line.split(':', 1)[1]}")
            else: manual_notes.append(line)
    
    all_notes = manual_notes + auto_notes
    if all_notes:
        notes_text = "\nNotes: <i>\n" + "\n".join(all_notes) + "</i>"
    else:
        notes_text = "\nNotes: <i>—</i>"
    # --- END OF NEW LOGIC ---

    footer_lines = [f"#{asset} #Signal #{side}"]

    return "\n".join([title_line] + body_lines + [targets_text] + [notes_text] + footer_lines)


# =-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=
# Other builders (Taken directly from your original, correct file)
# -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
def build_review_text(draft: dict) -> str:
    asset = (draft.get("asset", "") or "").upper()
    side = (draft.get("side", "") or "").upper()
    market = (draft.get("market", "") or "-")
    entry_scalar, zone = _entry_scalar_and_zone(draft.get("entry"))
    sl = float(draft.get("stop_loss", 0) or 0)
    raw = draft.get("targets"); tps: List[float] = []
    if isinstance(raw, str): raw = [x for x in raw.replace(",", " ").split() if x]
    for x in (raw or []):
        try: tps.append(float(x))
        except Exception: pass
    tp1 = float(tps[0]) if tps else None
    planned_rr = _rr(entry_scalar, sl, tp1, side)
    notes = draft.get("notes") or "-"
    lines_tps = "\n".join([f"• TP{i}: {tp:g}" for i, tp in enumerate(tps, start=1)]) or "—"
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
# --- END OF FINAL, COMPLETE, AND MERGED FILE ---```