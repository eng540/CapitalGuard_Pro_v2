# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any
from math import isfinite
from datetime import datetime, timezone
from capitalguard.domain.entities import RecommendationStatus

def _pct(entry: float, target: float, side: str) -> float:
    if not entry or entry == 0:
        return 0.0
    return ((target - entry) / entry * 100.0) if (side or "").upper() == "LONG" else ((entry - target) / entry * 100.0)

def _rr(entry: float, sl: float, tp1: Optional[float], side: str) -> str:
    try:
        risk = abs(entry - sl)
        if risk <= 0 or tp1 is None:
            return "—"
        reward = abs(tp1 - entry) if side.upper() == "LONG" else abs(entry - tp1)
        ratio = reward / risk
        return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception:
        return "—"

def _rr_actual(entry: float, sl: float, exit_price: Optional[float], side: str) -> str:
    try:
        if exit_price is None:
            return "—"
        risk = abs(entry - sl)
        if risk <= 0:
            return "—"
        reward = abs(exit_price - entry) if side.upper() == "LONG" else abs(entry - exit_price)
        ratio = reward / risk
        return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception:
        return "—"

def _format_targets(entry: float, side: str, tps: Iterable[float]) -> str:
    """Basic target formatter for PENDING or initial ACTIVE cards."""
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        try:
            pct = _pct(entry, float(tp), side)
            lines.append(f"• TP{i}: {float(tp):g} ({pct:+.2f}%)")
        except (ValueError, TypeError):
            continue
    return "\n".join(lines) if lines else "—"
    
def _format_targets_for_active_trade(entry: float, side: str, tps: List[float], live_price: float) -> str:
    """Advanced target formatter showing progress for ACTIVE trades."""
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        try:
            pct = _pct(entry, float(tp), side)
            total_dist = abs(tp - entry)
            current_dist = abs(live_price - entry)
            progress = min(100, (current_dist / total_dist) * 100) if total_dist > 0 else 0
            progress_bar = '█' * int(progress/10) + '─' * (10 - int(progress/10))
            lines.append(f"• TP{i}: {float(tp):g} ({pct:+.2f}%) - <i>[{progress_bar}] {progress:.0f}%</i>")
        except (ValueError, TypeError):
            continue
    return "\n".join(lines) if lines else "—"

def build_trade_card_text(rec) -> str:
    rec_id, asset, side = getattr(rec, "id", None), getattr(getattr(rec, "asset", None), "value", "N/A"), getattr(getattr(rec, "side", None), "value", "N/A")
    entry, sl = float(getattr(getattr(rec, "entry", None), "value", 0)), float(getattr(getattr(rec, "stop_loss", None), "value", 0))
    tps = list(getattr(getattr(rec, "targets", None), "values", []))
    status, live_price = getattr(rec, "status", RecommendationStatus.PENDING), getattr(rec, "live_price", None)
    now_utc = datetime.now(timezone.utc).strftime('%H:%M %Z')

    title_line = f"<b>{asset}</b> — {side}"
    if rec_id: title_line = f"Signal #{rec_id} | <b>{asset}</b> — {side}"

    body_lines, targets_lines = [], []
    if status == RecommendationStatus.PENDING:
        body_lines.append("Status: ⏳ <b>PENDING ENTRY</b>")
        if live_price:
            dist_pct = _pct(live_price, entry, "LONG")
            body_lines.append(f"<i>Live Price ({now_utc}): {live_price:g}</i>")
            body_lines.append(f"<i>Distance to Entry: {dist_pct:+.2f}%</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        targets_lines.append("<u>Targets (Plan)</u>:")
        targets_lines.append(_format_targets(entry, side, tps))
        
    elif status == RecommendationStatus.ACTIVE:
        body_lines.append("Status: 🟢 <b>ACTIVE</b>")
        if live_price:
            pnl = _pct(entry, live_price, side)
            body_lines.append(f"<i>Live Price ({now_utc}): {live_price:g} (PnL: {pnl:+.2f}%)</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        targets_lines.append("<u>Targets (Live Progress)</u>:")
        targets_lines.append(_format_targets_for_active_trade(entry, side, tps, live_price) if live_price else _format_targets(entry, side, tps))
        
    elif status == RecommendationStatus.CLOSED:
        exit_p = getattr(rec, 'exit_price', None)
        pnl = _pct(entry, exit_p, side) if exit_p else 0
        rr_act = _rr_actual(entry, sl, float(exit_p or 0), side)
        body_lines.append(f"Status: ✅ <b>CLOSED</b> at {exit_p:g}")
        result_line = f"Result: <b>Profit of {pnl:+.2f}%</b>" if pnl > 0 else f"Result: <b>Loss of {pnl:+.2f}%</b>"
        body_lines.append(f"{result_line} (R/R act: {rr_act})")

    notes = getattr(rec, "notes", None) or "—"
    footer_lines = [f"\nNotes: <i>{notes}</i>", f"#{asset} #Signal #{side}"]
    
    return "\n".join([title_line] + body_lines + targets_lines + footer_lines)

def build_review_text(draft: dict) -> str:
    asset = (draft.get("asset","") or "").upper(); side = (draft.get("side","") or "").upper()
    market = (draft.get("market","") or "-"); entry = float(draft.get("entry",0) or 0)
    sl = float(draft.get("stop_loss",0) or 0); raw = draft.get("targets")
    if isinstance(raw, str): raw = [x for x in raw.replace(",", " ").split() if x]
    tps: List[float] = [];
    for x in (raw or []):
        try: tps.append(float(x))
        except: pass
    tp1 = float(tps[0]) if tps else None
    planned_rr = _rr(entry, sl, tp1, side); notes = draft.get("notes") or "-"
    lines_tps = "\n".join([f"• TP{i}: {tp:g}" for i,tp in enumerate(tps, start=1)]) or "—"
    return ("📝 <b>مراجعة التوصية</b>\n\n"
            f"<b>{asset}</b> | {market} / {side}\n"
            f"Entry 💰: {entry:g}\n"
            f"SL 🛑: {sl:g}\n"
            f"<u>Targets</u>:\n{lines_tps}\n\n"
            f"R/R (plan): <b>{planned_rr}</b>\n"
            f"ملاحظات: <i>{notes}</i>\n\n"
            "هل تريد نشر هذه التوصية في القناة؟")

def build_review_text_with_price(draft: dict, preview_price: float | None) -> str:
    base = build_review_text(draft)
    if preview_price is None: return base + "\n\n🔎 Current Price: —"
    return base + f"\n\n🔎 Current Price: <b>{preview_price:g}</b>"

def build_analyst_stats_text(stats: Dict[str, Any]) -> str:
    total = stats.get('total_recommendations', 0); open_recs = stats.get('open_recommendations', 0)
    closed_recs = stats.get('closed_recommendations', 0); win_rate = stats.get('overall_win_rate', '0.00%')
    total_pnl = stats.get('total_pnl_percent', '0.00%')
    lines = [
        "📊 <b>Your Performance Summary</b> 📊", "─" * 15,
        f"Total Recommendations: <b>{total}</b>",
        f"Open Trades: <b>{open_recs}</b>", f"Closed Trades: <b>{closed_recs}</b>",
        "─" * 15, f"Overall Win Rate: <b>{win_rate}</b>",
        f"Total PnL (Cumulative %): <b>{total_pnl}</b>", "─" * 15,
        f"<i>Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
    ]
    return "\n".join(lines)
# --- END OF FILE ---```