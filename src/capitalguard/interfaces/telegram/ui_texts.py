# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any, Tuple
from math import isfinite
from datetime import datetime, timezone
from capitalguard.domain.entities import RecommendationStatus

# ============================================================
# Helpers
# ============================================================

def _pct(entry: float, target: float, side: str) -> float:
    """
    نسبة PnL % مبسّطة:
    - للـ LONG: (target-entry)/entry*100
    - للـ SHORT: (entry-target)/entry*100
    ملاحظة: تُستخدم أيضًا في لوحات أخرى (مستدعاة من keyboards.py).
    """
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
    """عرض الأهداف بصيغة بسيطة (للبطاقات المعلقة أو عند عدم وجود سعر حي)."""
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        try:
            tp_f = float(tp)
            pct = _pct(entry, tp_f, side)
            lines.append(f"• TP{i}: {tp_f:g} ({pct:+.2f}%)")
        except (ValueError, TypeError):
            continue
    return "\n".join(lines) if lines else "—"


def _format_targets_for_active_trade(entry: float, side: str, tps: List[float], live_price: float) -> str:
    """عرض الأهداف مع شريط تقدّم تقريبي (للتوصيات النشطة مع سعر حي)."""
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        try:
            tp_f = float(tp)
            pct = _pct(entry, tp_f, side)
            total_dist = abs(tp_f - entry)
            current_dist = abs(live_price - entry)
            progress = min(100, (current_dist / total_dist) * 100) if total_dist > 0 else 0
            blocks = int(progress / 10)
            progress_bar = '█' * blocks + '─' * (10 - blocks)
            lines.append(f"• TP{i}: {tp_f:g} ({pct:+.2f}%) - <i>[{progress_bar}] {progress:.0f}%</i>")
        except (ValueError, TypeError):
            continue
    return "\n".join(lines) if lines else "—"


def _entry_scalar_and_zone(entry_val: Any) -> Tuple[float, Optional[Tuple[float, float]]]:
    """
    يدعم أن يكون entry:
      - رقمًا مفردًا (float/int)
      - قائمة/تابل تمثل منطقة دخول [low, ..., high]
    يُرجع (entry_scalar, zone):
      - entry_scalar: قيمة رقمية آمنة للحساب والعرض (الأول عند وجود قائمة)
      - zone: (min,max) إن وُجدت منطقة، وإلا None
    """
    # قائمة/منطقة
    if isinstance(entry_val, (list, tuple)) and entry_val:
        try:
            first = float(entry_val[0])
            last = float(entry_val[-1])
            lo, hi = (first, last) if first <= last else (last, first)
            return first, (lo, hi)
        except Exception:
            # فشل التحويل الكامل، حاول أقل شيء ممكن
            try:
                return float(entry_val[0]), None
            except Exception:
                return 0.0, None
    # رقم مفرد أو None
    try:
        return float(entry_val or 0), None
    except Exception:
        return 0.0, None


# ============================================================
# Cards/Text builders
# ============================================================

def build_trade_card_text(rec) -> str:
    """
    بطاقة الإشارة (عام/خاص) — تحافظ على التنسيق السابق مع تحسينات طفيفة.
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
    targets_lines: List[str] = []

    if status == RecommendationStatus.PENDING:
        body_lines.append("Status: ⏳ <b>PENDING ENTRY</b>")
        if live_price:
            # المسافة التقريبية بين السعر الحي والدخول (طبيعتها Long لمؤشر القرب فقط)
            try:
                dist_pct = _pct(entry, float(live_price), "LONG")
                body_lines.append(f"<i>Live Price ({now_utc}): {float(live_price):g}</i>")
                body_lines.append(f"<i>Distance to Entry: {dist_pct:+.2f}%</i>")
            except Exception:
                body_lines.append(f"<i>Live Price ({now_utc}): {live_price}</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        targets_lines.append("<u>Targets (Plan)</u>:")
        targets_lines.append(_format_targets(entry, side, tps))

    elif status == RecommendationStatus.ACTIVE:
        body_lines.append("Status: 🟢 <b>ACTIVE</b>")
        if live_price:
            try:
                pnl = _pct(entry, float(live_price), side)
                body_lines.append(f"<i>Live Price ({now_utc}): {float(live_price):g} (PnL: {pnl:+.2f}%)</i>")
            except Exception:
                body_lines.append(f"<i>Live Price ({now_utc}): {live_price}</i>")
        body_lines.append(f"\nEntry 💰: {entry:g}")
        body_lines.append(f"SL 🛑: {sl:g}")
        targets_lines.append("<u>Targets (Live Progress)</u>:")
        if live_price is not None:
            try:
                targets_lines.append(_format_targets_for_active_trade(entry, side, tps, float(live_price)))
            except Exception:
                targets_lines.append(_format_targets(entry, side, tps))
        else:
            targets_lines.append(_format_targets(entry, side, tps))

    elif status == RecommendationStatus.CLOSED:
        exit_p = getattr(rec, 'exit_price', None)
        pnl = _pct(entry, float(exit_p), side) if exit_p is not None else 0.0
        rr_act = _rr_actual(entry, sl, float(exit_p or 0), side)
        body_lines.append(f"Status: ✅ <b>CLOSED</b> at {float(exit_p):g}" if exit_p is not None else "Status: ✅ <b>CLOSED</b>")
        result_line = f"Result: <b>Profit of {pnl:+.2f}%</b>" if pnl > 0 else f"Result: <b>Loss of {pnl:+.2f}%</b>"
        body_lines.append(f"{result_line} (R/R act: {rr_act})")

    notes = getattr(rec, "notes", None) or "—"
    footer_lines = [f"\nNotes: <i>{notes}</i>", f"#{asset} #Signal #{side}"]

    return "\n".join([title_line] + body_lines + targets_lines + footer_lines)


def build_review_text(draft: dict) -> str:
    """
    مراجعة التوصية قبل النشر:
    - تدعم entry كرقم أو كمنطقة [lo, hi, ...] بدون رميات أخطاء.
    - تُحافظ على نفس الشكل السابق مع سطر إضافي إذا كانت منطقة دخول.
    """
    asset = (draft.get("asset", "") or "").upper()
    side = (draft.get("side", "") or "").upper()
    market = (draft.get("market", "") or "-")

    entry_scalar, zone = _entry_scalar_and_zone(draft.get("entry"))
    sl = float(draft.get("stop_loss", 0) or 0)

    # تجميع الأهداف (قد تأتي كسلسلة نصية)
    raw = draft.get("targets")
    if isinstance(raw, str):
        raw = [x for x in raw.replace(",", " ").split() if x]
    tps: List[float] = []
    for x in (raw or []):
        try:
            tps.append(float(x))
        except Exception:
            pass

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
    """
    نفس مراجعة التوصية مع سطر “السعر الحالي” إن توفّر.
    يدعم أيضًا حالة entry كمنطقة؛ في هذه الحالة يُستخدم entry_scalar (أول عنصر) للعرض والحسابات الأخرى خارج هذا الملف.
    """
    base = build_review_text(draft)
    if preview_price is None:
        return base + "\n\n🔎 Current Price: —"
    try:
        return base + f"\n\n🔎 Current Price: <b>{float(preview_price):g}</b>"
    except Exception:
        return base + f"\n\n🔎 Current Price: <b>{preview_price}</b>"


def build_analyst_stats_text(stats: Dict[str, Any]) -> str:
    total = stats.get('total_recommendations', 0)
    open_recs = stats.get('open_recommendations', 0)
    closed_recs = stats.get('closed_recommendations', 0)
    win_rate = stats.get('overall_win_rate', '0.00%')
    total_pnl = stats.get('total_pnl_percent', '0.00%')

    lines = [
        "📊 <b>Your Performance Summary</b> 📊",
        "─" * 15,
        f"Total Recommendations: <b>{total}</b>",
        f"Open Trades: <b>{open_recs}</b>",
        f"Closed Trades: <b>{closed_recs}</b>",
        "─" * 15,
        f"Overall Win Rate: <b>{win_rate}</b>",
        f"Total PnL (Cumulative %): <b>{total_pnl}</b>",
        "─" * 15,
        f"<i>Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>",
    ]
    return "\n".join(lines)
# --- END OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---