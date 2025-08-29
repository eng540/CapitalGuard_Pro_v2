# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable
from capitalguard.domain.entities import Recommendation

def _pct(cur: float, base: float) -> str:
    try:
        return f"{(cur-base)/base*100:.2f}%"
    except Exception:
        return "—"

def build_trade_card_text(rec: Recommendation) -> str:
    """
    نص بطاقة القناة (لا أزرار).
    """
    symbol = getattr(rec.asset, "value", rec.asset)
    side   = getattr(rec.side, "value", rec.side)
    tps: Iterable[float] = getattr(rec.targets, "values", rec.targets) or []
    entry = getattr(rec.entry, "value", rec.entry)
    sl    = getattr(rec.stop_loss, "value", rec.stop_loss)
    status= rec.status.upper()

    lines = []
    lines.append(f"📣 <b>Trade Signal — #REC{rec.id:04d}</b>  |  <b>#{symbol}</b> #Signal #{getattr(rec.market,'title',lambda:'')() or (rec.market or 'Futures')} #{side}")
    lines.append("────────────────────────")
    lines.append(f"💎 <b>Symbol</b> : <code>{symbol}</code>")
    lines.append(f"📌 <b>Type</b>   : <code>{(rec.market or 'Futures').title()} / {side}</code>")
    lines.append("────────────────────────")
    lines.append(f"💰 <b>Entry</b>  : <code>{entry}</code>")
    lines.append(f"🛑 <b>SL</b>     : <code>{sl}</code>")
    lines.append("")
    lines.append("🎯 <b>TPs</b>")
    for i, tp in enumerate(tps, start=1):
        lines.append(f"• TP{i}: <code>{tp}</code> (+{_pct(float(tp), float(entry))})")
    lines.append("")
    lines.append("📊 <b>R/R</b>   : —")
    if rec.notes:
        lines.append(f"📝 <b>Notes</b> : {rec.notes}")
    lines.append("")
    if status == "CLOSED":
        exit_p = rec.exit_price if rec.exit_price is not None else "—"
        lines.append(f"✅ <b>Closed at:</b> <code>{exit_p}</code>")
        lines.append("")
    lines.append("(Disclaimer: Not financial advice. Manage your risk.)")
    lines.append("")
    lines.append("🔗 <i>Crypto Radar Bot</i>  |  📣 <i>Official Channel</i>  |  📬 <i>Contact for subscription</i>")
    return "\n".join(lines)

def build_panel_caption(rec: Recommendation) -> str:
    """
    عنوان لوحة التحكّم داخل المحادثة.
    """
    symbol = getattr(rec.asset, "value", rec.asset)
    side   = getattr(rec.side, "value", rec.side)
    entry  = getattr(rec.entry, "value", rec.entry)
    sl     = getattr(rec.stop_loss, "value", rec.stop_loss)
    tps    = getattr(rec.targets, "values", rec.targets) or []
    st     = rec.status.upper()
    tps_txt = " • ".join(str(x) for x in tps) if tps else "—"
    return (
        f"<b>#{rec.id} — {symbol}</b>\n"
        f"الحالة: <b>{st}</b>\n"
        f"الدخول: <code>{entry}</code>\n"
        f"وقف الخسارة: <code>{sl}</code>\n"
        f"الأهداف: <code>{tps_txt}</code>"
    )

def build_close_summary(rec: Recommendation) -> str:
    symbol = getattr(rec.asset, "value", rec.asset)
    entry  = float(getattr(rec.entry, "value", rec.entry))
    exit_p = float(rec.exit_price or 0.0)
    pnl    = exit_p - entry if rec.side.value == "LONG" else (entry - exit_p)
    pnl_pct= (pnl / entry * 100.0) if entry else 0.0
    return (
        f"✅ تم إغلاق التوصية <b>#{rec.id}</b>\n"
        f"• <b>{symbol}</b>\n"
        f"• الدخول: <code>{entry}</code>\n"
        f"• الخروج: <code>{exit_p}</code>\n"
        f"• العائد التقريبي: <b>{pnl_pct:.2f}%</b>"
    )
# --- END OF FILE ---