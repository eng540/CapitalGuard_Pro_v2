# --- START OF COMPLETE MODIFIED FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any, Tuple
from math import isfinite
from datetime import datetime, timezone

from capitalguard.domain.entities import Recommendation, RecommendationStatus

def _pct(entry: float, exit_val: float, side: str) -> float:
    """Calculates percentage change."""
    if not all(map(isfinite, [entry, exit_val])) or entry == 0:
        return 0.0
    val = (exit_val / entry - 1) * 100
    return val if side.upper() == "LONG" else -val

def _rr(entry: float, sl: float, tp: float, side: str) -> float:
    """Calculates Risk/Reward ratio."""
    if not all(map(isfinite, [entry, sl, tp])) or entry == sl:
        return 0.0
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        return float("inf")
    return reward / risk

def build_trade_card_text(rec: Recommendation) -> str:
    """
    Builds the complete, professionally formatted text for a recommendation card.
    It now intelligently parses notes and displays a clean, structured summary.
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

    # --- Header ---
    title_line = f"<b>Signal #{rec_id} | {asset} — {side}</b>"

    # --- Status & PnL Block ---
    status_lines: List[str] = []
    if status == RecommendationStatus.PENDING:
        status_lines.append("Status: ⏳ <b>معلقة</b>")
    elif status == RecommendationStatus.ACTIVE:
        status_lines.append("Status: 🟢 <b>نشطة</b>")
        if live_price and isfinite(live_price):
            try:
                pnl = _pct(entry, float(live_price), side)
                status_lines.append(f"<i>Live Price ({now_utc}): {float(live_price):g} (PnL: {pnl:+.2f}%)</i>")
            except (ValueError, TypeError):
                pass
    elif status == RecommendationStatus.CLOSED:
        exit_p = getattr(rec, 'exit_price', None)
        status_lines.append(f"Status: ✅ <b>مغلقة عند {exit_p:g}</b>")
        if exit_p is not None and isfinite(exit_p):
            pnl = _pct(entry, float(exit_p), side)
            result_text = f"بربح <b>{pnl:+.2f}%</b>" if pnl >= 0 else f"بخسارة <b>{pnl:+.2f}%</b>"
            status_lines.append(f"Result: {result_text}")
        else:
            status_lines.append("Result: —")

    # --- Core Trade Info ---
    trade_info_lines = [
        "",
        f"<b>Entry 💰:</b> <code>{entry:g}</code>",
        f"<b>SL 🛑:</b> <code>{sl:g}</code>",
        "<b>Targets 🎯:</b>"
    ]
    for i, tp in enumerate(tps, start=1):
        progress_bar = ""
        if status == RecommendationStatus.ACTIVE and live_price and isfinite(live_price):
            try:
                # Calculate progress
                start = entry
                end = tp
                current = float(live_price)
                if end == start:
                    progress = 1.0 if current >= end else 0.0
                else:
                    progress = (current - start) / (end - start)
                
                if side.upper() == "SHORT":
                    progress = (start - current) / (start - end)
                
                progress = max(0.0, min(1.0, progress)) # Clamp between 0 and 1
                
                filled_blocks = int(progress * 10)
                empty_blocks = 10 - filled_blocks
                progress_bar = f" [{'█' * filled_blocks}{'─' * empty_blocks}] {progress:.0%}"
            except Exception:
                progress_bar = "" # Ignore errors in progress bar calculation
        trade_info_lines.append(f"• TP{i}: <code>{tp:g}</code> ({_pct(entry, tp, side):+.2f}%)" + progress_bar)


    # --- Notes Parsing and Formatting ---
    notes_section_lines: List[str] = []
    manual_notes = []
    auto_notes = []
    if rec.notes:
        for line in rec.notes.strip().split('\n'):
            line = line.strip()
            if not line: continue
            
            if line.startswith("[SL_UPDATE]:"):
                val = line.split(":", 1)[1]
                if val == str(entry):
                    auto_notes.append(f"<i>- تم نقل الوقف إلى نقطة الدخول.</i>")
                else:
                    auto_notes.append(f"<i>- تم تحديث الوقف إلى {val}.</i>")
            elif line.startswith("[TP_UPDATE]:"):
                auto_notes.append(f"<i>- تم تحديث الأهداف.</i>")
            elif line.startswith("[PARTIAL_CLOSE]:"):
                val = line.split(":", 1)[1]
                auto_notes.append(f"<i>- تم إغلاق 50% من الصفقة في {val}.</i>")
            else:
                manual_notes.append(line)

    if manual_notes:
        notes_section_lines.append("\n<b>Notes:</b>")
        notes_section_lines.extend(manual_notes)
    
    if auto_notes:
        if not manual_notes:
            notes_section_lines.append("\n<b>Notes:</b>")
        notes_section_lines.extend(auto_notes)

    # --- Footer ---
    footer_lines = [f"\n#{asset} #Signal #{side}"]

    # --- Assembly ---
    all_parts = [
        title_line,
        *status_lines,
        *trade_info_lines,
        *notes_section_lines,
        *footer_lines
    ]
    return "\n".join(all_parts)
# --- END OF COMPLETE MODIFIED FILE ---