# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, Optional
from capitalguard.domain.entities import Recommendation

# تنسيقات عامة
BOLD = "<b>{}</b>"
MONO = "<code>{}</code>"
HR   = "─" * 40

FOOTER = (
    "\n\n"
    "🔗 <b>Crybto Radar Bot</b>  |  📣 <b>Official Channel</b>  |  📬 <b>Contact for subscription</b>"
)

def _fmt_pct(base: float, target: float, side: str) -> str:
    try:
        if base == 0:
            return "0.00%"
        diff = (target - base) / base if side.upper() == "LONG" else (base - target) / base
        return f"{diff * 100:.2f}%"
    except Exception:
        return "-"

def _iter_targets(v: Iterable[float]) -> list[float]:
    # يُضمن أن targets قابلة للتكرار
    return list(v or [])

def build_trade_card_text(rec: Recommendation) -> str:
    """بطاقة النشر في القناة (بدون أزرار)."""
    asset = getattr(rec.asset, "value", rec.asset)
    side  = getattr(rec.side,  "value", rec.side)
    entry = float(getattr(rec.entry, "value", rec.entry))
    sl    = float(getattr(rec.stop_loss, "value", rec.stop_loss))
    tps   = _iter_targets(getattr(rec.targets, "values", rec.targets))
    mkt   = rec.market or "Futures"

    # ترويسة مع هاشتاقات
    rec_code = f"#REC{rec.id:04d}" if rec.id else "#REC"
    head = (
        f"📣 <b>Trade Signal</b> — {rec_code}  |  "
        f"#{str(asset).upper()} #Signal #{mkt} #{side.upper()}\n"
        f"{'└' + '─'*22 + '┘'}"
    )

    # الجسم
    body = [
        f"💎 {BOLD.format('Symbol')} : {str(asset).upper()}",
        f"📌 {BOLD.format('Type')}   : {mkt} / {side.upper()}",
        HR,
        f"💰 {BOLD.format('Entry')}  : {entry:g}",
        f"🛑 {BOLD.format('SL')}     : {sl:g}",
        "",
        f"🎯 {BOLD.format('TPs')}",
    ]
    for i, tp in enumerate(tps, start=1):
        inc = _fmt_pct(entry, float(tp), side)
        body.append(f"• TP{i}: {float(tp):g} ({inc})")

    body += [
        "",
        HR,
        f"📊 {BOLD.format('R/R')}   : —",
        f"📝 {BOLD.format('Notes')} : {rec.notes or '—'}",
        "\n(Disclaimer: Not financial advice. Manage your risk.)",
    ]

    # إغلاق إن كان مغلقًا
    if str(rec.status).upper() == "CLOSED":
        ep = rec.exit_price if rec.exit_price is not None else "—"
        body.append(f"\n✅ <b>Closed at</b>: {ep}")

    return head + "\n" + "\n".join(body) + FOOTER

def build_admin_panel_caption(rec: Recommendation) -> str:
    """نص لوحة التحكم الخاصة (DM للإدارة)."""
    asset = getattr(rec.asset, "value", rec.asset)
    side  = getattr(rec.side,  "value", rec.side)
    return f"لوحة تحكم #REC{rec.id:04d} — {str(asset).upper()} ({side})"
# --- END OF FILE ---