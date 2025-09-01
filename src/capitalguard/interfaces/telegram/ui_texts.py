# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List, Optional
from math import isfinite

# ... (الدوال المساعدة _pct, _format_targets, _rr, _rr_actual تبقى كما هي) ...
def _pct(entry: float, target: float, side: str) -> float:
    if not entry: return 0.0
    return (target - entry) / entry * 100.0 if (side or "").upper() == "LONG" else (entry - target) / entry * 100.0

def _format_targets(entry: float, side: str, tps: Iterable[float]) -> str:
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        pct = _pct(entry, float(tp), side)
        lines.append(f"• TP{i}: {float(tp):g} ({pct:+.2f}%)")
    return "\n".join(lines) if lines else "—"

def _rr(entry: float, sl: float, tp1: Optional[float], side: str) -> str:
    try:
        risk = abs(entry - sl)
        if risk <= 0 or tp1 is None: return "—"
        reward = abs(tp1 - entry) if side.upper() == "LONG" else abs(entry - tp1)
        ratio = reward / risk
        return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception: return "—"

def _rr_actual(entry: float, sl: float, exit_price: Optional[float], side: str) -> str:
    try:
        if exit_price is None: return "—"
        risk = abs(entry - sl)
        if risk <= 0: return "—"
        reward = abs(exit_price - entry) if side.upper() == "LONG" else abs(entry - exit_price)
        ratio = reward / risk
        return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception: return "—"

def build_trade_card_text(rec) -> str:
    rec_id = getattr(rec, "id", None) # نحصل على الـ ID
    asset = getattr(rec.asset, "value", rec.asset)
    side  = getattr(rec.side, "value", rec.side)
    entry = float(getattr(rec.entry, "value", rec.entry))
    sl    = float(getattr(rec.stop_loss, "value", rec.stop_loss))
    tps   = list(getattr(rec.targets, "values", rec.targets or []))
    tp1   = float(tps[0]) if tps else None
    
    # ✅ تعديل: بناء العنوان بناءً على وجود الـ ID
    title_line = f"<b>{asset}</b> — {side}"
    if rec_id:
        title_line = f"Signal #{rec_id} | <b>{asset}</b> — {side}"

    planned_rr = _rr(entry, sl, tp1, side)
    status = str(getattr(rec, "status", "OPEN")).upper()
    if status == "CLOSED":
        exit_p = getattr(rec, 'exit_price', None)
        rr_act = _rr_actual(entry, sl, float(exit_p or 0), side)
        status_line = f"✅ <b>CLOSED</b> at {exit_p} (R/R act: {rr_act})"
    else:
        status_line = "🟢 <b>OPEN</b>"

    notes = getattr(rec, "notes", None) or "—"
    
    # ✅ تعديل: تضمين العنوان الجديد في النص النهائي
    return (
        f"{title_line}\n"
        f"Status: {status_line}\n\n"
        f"Entry 💰: {entry:g}\n"
        f"SL 🛑: {sl:g}\n"
        f"<u>Targets</u>:\n{_format_targets(entry, side, tps)}\n\n"
        f"R/R (plan): <b>{planned_rr}</b>\n"
        f"Notes: <i>{notes}</i>\n\n"
        f"#{asset} #Signal #{side}"
    )

def build_review_text(draft: dict) -> str:
    # ... (هذه الدالة لا تتغير)
    asset = (draft.get("asset","") or "").upper()
    side = (draft.get("side","") or "").upper()
    market = (draft.get("market","") or "-")
    entry = float(draft.get("entry",0) or 0)
    sl    = float(draft.get("stop_loss",0) or 0)
    raw = draft.get("targets")
    if isinstance(raw, str):
        raw = [x for x in raw.replace(",", " ").split() if x]
    tps: List[float] = []
    for x in (raw or []):
        try: tps.append(float(x))
        except: pass
    tp1 = float(tps[0]) if tps else None
    planned_rr = _rr(entry, sl, tp1, side)
    notes = draft.get("notes") or "-"
    lines_tps = "\n".join([f"• TP{i}: {tp:g}" for i,tp in enumerate(tps, start=1)]) or "—"
    return (
        "📝 <b>مراجعة التوصية</b>\n\n"
        f"<b>{asset}</b> | {market} / {side}\n"
        f"Entry 💰: {entry:g}\n"
        f"SL 🛑: {sl:g}\n"
        f"<u>Targets</u>:\n{lines_tps}\n\n"
        f"R/R (plan): <b>{planned_rr}</b>\n"
        f"ملاحظات: <i>{notes}</i>\n\n"
        "هل تريد نشر هذه التوصية في القناة؟"
    )

def build_review_text_with_price(draft: dict, preview_price: float | None) -> str:
    # ... (هذه الدالة لا تتغير)
    base = build_review_text(draft)
    if preview_price is None:
        return base + "\n🔎 Current Price: —"
    return base + f"\n🔎 Current Price: <b>{preview_price:g}</b>"
# --- END OF FILE ---