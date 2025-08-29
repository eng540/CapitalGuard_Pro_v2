# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Any, Iterable, List

def _as_str(v: Any) -> str:
    """يعيد القيمة كسلسلة، ويدعم كائنات Enum/Pydantic (value)."""
    if v is None:
        return "-"
    return str(getattr(v, "value", v))

def _as_float(v: Any) -> float | None:
    """يحاول تحويل أي قيمة رقمية (أو مغلّفة) إلى float بأمان."""
    if v is None:
        return None
    try:
        v0 = getattr(v, "value", v)
        return float(v0)
    except Exception:
        return None

def _as_list_of_floats(v: Any) -> List[float]:
    """
    يحوّل targets إلى list[float] مهما كان شكلها:
    - list/tuple
    - كائن فيه .values أو .items
    - سلسلة أرقام مفصولة بفواصل/مسافات (احتياط)
    - None → []
    """
    if v is None:
        return []

    # targets مغلف بـ .values
    if hasattr(v, "values"):
        try:
            seq = getattr(v, "values")
            if isinstance(seq, dict):
                seq = seq.values()
            return [float(x) for x in list(seq)]
        except Exception:
            pass

    # Iterable مباشر
    if isinstance(v, (list, tuple, set)):
        out: List[float] = []
        for x in v:
            try:
                out.append(float(getattr(x, "value", x)))
            except Exception:
                continue
        return out

    # نص مفصول
    if isinstance(v, str):
        tokens = v.replace(",", " ").split()
        out = []
        for t in tokens:
            try:
                out.append(float(t))
            except Exception:
                continue
        return out

    # محاولة أخيرة: عنصر واحد
    try:
        f = float(getattr(v, "value", v))
        return [f]
    except Exception:
        return []

def _pct(entry: float | None, target: float | None, side: str) -> str:
    """نسبة التغير من الدخول إلى الهدف وفق الاتجاه."""
    if entry is None or target is None or entry == 0:
        return "-"
    if side.upper() == "LONG":
        p = (target - entry) / entry * 100.0
    else:
        p = (entry - target) / entry * 100.0
    return f"{p:.2f}%"

def build_trade_card_text(rec) -> str:
    """
    يبني بطاقة توصية غنية ومرتّبة (HTML) قابلة للنشر في القناة.
    يتسامح مع أنواع الحقول المختلفة (ORM/Pydantic/Enums/Value Objects).
    """
    # حقول أساسية
    rid   = getattr(rec, "id", None) or 0
    asset = _as_str(getattr(rec, "asset", "-")).upper()
    side  = _as_str(getattr(rec, "side", "-")).upper()
    rtype = _as_str(getattr(rec, "type", "Spot"))  # Spot/Futures إن توفر
    status= _as_str(getattr(rec, "status", "OPEN")).upper()

    entry = _as_float(getattr(rec, "entry", None))
    sl    = _as_float(getattr(rec, "stop_loss", None))
    tps   = _as_list_of_floats(getattr(rec, "targets", None))
    exitp = _as_float(getattr(rec, "exit_price", None))
    notes = getattr(rec, "notes", None)
    notes_str = str(notes).strip() if notes not in (None, "", "-", "None") else "-"

    # تنسيق TPs كسطور
    tp_lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        pct = _pct(entry, tp, side) if entry is not None else "-"
        tp_lines.append(f"• TP{i}: {tp:g} ({pct})")
    tps_block = "\n".join(tp_lines) if tp_lines else "-"

    header = (
        f"📣 <b>Trade Signal — #REC{rid:04d}</b>  |  "
        f"#{asset} #Signal #{rtype.capitalize()} #{side.capitalize()}"
    )
    lines = [
        header,
        "────────────────────────",
        f"💎 <b>Symbol</b> : {asset}",
        f"📌 <b>Type</b>   : {rtype} / {side}",
        "────────────────────────",
        f"💰 <b>Entry</b>  : {entry if entry is not None else '-'}",
        f"🛑 <b>SL</b>     : {sl if sl is not None else '-'}",
        "",
        "🎯 <b>TPs</b>",
        tps_block,
        "────────────────────────",
        "📊 <b>R/R</b>   : -",
        f"📝 <b>Notes</b> : {notes_str}",
        "",
        "(Disclaimer: Not financial advice. Manage your risk.)",
    ]

    # سطر الحالة/الخروج إن كانت مغلقة
    if status == "CLOSED":
        lines.append(f"\n✅ <b>Closed</b> — #{rid:04d}")
        if exitp is not None:
            lines.append(f"• {asset} @ {exitp:g}")

    return "\n".join(lines)