# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any
from math import isfinite
from datetime import datetime, timezone
# ✅ --- Import the new Enum ---
from capitalguard.domain.entities import RecommendationStatus

# --- (Helper functions _pct, _rr, _rr_actual remain the same) ---
def _pct(entry: float, target: float, side: str) -> float: # ...unchanged
    if not entry or entry == 0: return 0.0
    return ((target - entry) / entry * 100.0) if (side or "").upper() == "LONG" else ((entry - target) / entry * 100.0)
def _rr(entry: float, sl: float, tp1: Optional[float], side: str) -> str: # ...unchanged
    try:
        risk = abs(entry - sl);
        if risk <= 0 or tp1 is None: return "—"
        reward = abs(tp1 - entry) if side.upper() == "LONG" else abs(entry - tp1)
        ratio = reward / risk; return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception: return "—"
def _rr_actual(entry: float, sl: float, exit_price: Optional[float], side: str) -> str: # ...unchanged
    try:
        if exit_price is None: return "—"
        risk = abs(entry - sl);
        if risk <= 0: return "—"
        reward = abs(exit_price - entry) if side.upper() == "LONG" else abs(entry - exit_price)
        ratio = reward / risk; return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception: return "—"
    
def _format_targets_for_active_trade(entry: float, side: str, tps: List[float], live_price: float) -> str:
    """Formats targets and shows progress towards them."""
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        try:
            pct = _pct(entry, float(tp), side)
            # Calculate progress
            total_dist = abs(tp - entry)
            current_dist = abs(live_price - entry)
            progress = min(100, (current_dist / total_dist) * 100) if total_dist > 0 else 0
            
            lines.append(f"• TP{i}: {float(tp):g} ({pct:+.2f}%) - <i>[{'█' * int(progress/10)}{'─' * (10 - int(progress/10))}] {progress:.0f}%</i>")
        except (ValueError, TypeError): continue
    return "\n".join(lines) if lines else "—"

def build_trade_card_text(rec) -> str:
    # --- Data Extraction ---
    rec_id = getattr(rec, "id", None)
    asset = getattr(getattr(rec, "asset", None), "value", "N/A")
    side = getattr(getattr(rec, "side", None), "value", "N/A")
    entry = float(getattr(getattr(rec, "entry", None), "value", 0))
    sl = float(getattr(getattr(rec, "stop_loss", None), "value", 0))
    tps = list(getattr(getattr(rec, "targets", None), "values", []))
    status = getattr(rec, "status", RecommendationStatus.PENDING)
    live_price = getattr(rec, "live_price", None)
    now_utc = datetime.now(timezone.utc).strftime('%H:%M %Z')

    # --- Title ---
    title_line = f"<b>{asset}</b> — {side}"
    if rec_id: title_line = f"Signal #{rec_id} | <b>{asset}</b> — {side}"

    # --- Main Body (changes based on status) ---
    body_lines = []
    if status == RecommendationStatus.PENDING:
        body_lines.append("Status: ⏳ <b>PENDING ENTRY</b>")
        if live_price:
            dist_pct = _pct(live_price, entry, "LONG") # Direction doesn't matter for distance %
            body_lines.append(f"<i>Live Price ({now_utc}): {live_price:g}</i>")
            body_lines.append(f"<i>Distance to Entry: {dist_pct:+.2f}%</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        
    elif status == RecommendationStatus.ACTIVE:
        body_lines.append("Status: 🟢 <b>ACTIVE</b>")
        if live_price:
            pnl = _pct(entry, live_price, side)
            body_lines.append(f"<i>Live Price ({now_utc}): {live_price:g} (PnL: {pnl:+.2f}%)</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        body_lines.append(f"<u>Targets</u>:")
        if live_price:
            body_lines.append(_format_targets_for_active_trade(entry, side, tps, live_price))
        
    elif status == RecommendationStatus.CLOSED:
        exit_p = getattr(rec, 'exit_price', None)
        pnl = _pct(entry, exit_p, side) if exit_p else 0
        rr_act = _rr_actual(entry, sl, float(exit_p or 0), side)
        status_line = f"✅ <b>CLOSED</b> at {exit_p:g}"
        result_line = f"Result: <b>Profit of {pnl:+.2f}%</b> (R/R act: {rr_act})" if pnl > 0 else f"Result: <b>Loss of {pnl:+.2f}%</b> (R/R act: {rr_act})"
        body_lines.append(status_line)
        body_lines.append(result_line)
    
    # --- Footer ---
    notes = getattr(rec, "notes", None) or "—"
    footer_lines = [
        f"\nNotes: <i>{notes}</i>",
        f"#{asset} #Signal #{side}"
    ]
    
    # --- Assemble Final Card ---
    return "\n".join([title_line] + body_lines + footer_lines)

# --- (build_review_text and build_review_text_with_price are unchanged) ---
# ...
# --- END OF FILE ---