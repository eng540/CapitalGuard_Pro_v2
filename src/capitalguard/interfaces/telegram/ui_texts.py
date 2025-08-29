# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable

WELCOME = (
    "👋 أهلاً بك في <b>CapitalGuard Bot</b>.\n"
    "ابدأ بـ <code>/newrec</code> لإنشاء توصية جديدة، أو <code>/open</code> لإدارة المفتوحة."
)

HELP = (
    "<b>الأوامر المتاحة:</b>\n\n"
    "• <code>/newrec</code> — إنشاء توصية تفاعليًا (أزرار + إدخالات)\n"
    "• <code>/open</code> — عرض/إدارة التوصيات المفتوحة\n"
    "• <code>/list</code> — إحصاء سريع\n"
    "• <code>/analytics</code> — ملخص أداء\n"
    "• <code>/ping</code> — فحص اتصال"
)

def _fmt_targets(targets: Iterable[float], entry: float | None = None) -> str:
    out = []
    for i, t in enumerate(targets, 1):
        if entry:
            pct = (t - entry) / entry * 100 if entry != 0 else 0.0
            out.append(f"• TP{i}: {t:g} ({pct:+.1f}%)")
        else:
            out.append(f"• TP{i}: {t:g}")
    return "\n".join(out) if out else "—"

def build_trade_card_text(rec) -> str:
    """
    بطاقة القناة (نص فقط، بلا أزرار).
    """
    asset = getattr(getattr(rec, "asset", ""), "value", getattr(rec, "asset", ""))
    side  = getattr(getattr(rec, "side", ""),  "value", getattr(rec, "side", ""))
    entry = float(getattr(getattr(rec, "entry", ""), "value", getattr(rec, "entry", 0.0)) or 0.0)
    sl    = float(getattr(getattr(rec, "stop_loss", ""), "value", getattr(rec, "stop_loss", 0.0)) or 0.0)
    tps   = list(getattr(getattr(rec, "targets", ""), "values", getattr(rec, "targets", []) ) or [])
    market= getattr(rec, "market", None) or "Futures"
    status= getattr(rec, "status", "OPEN")
    exitp = getattr(rec, "exit_price", None)

    header = f"📣 Trade Signal — #{rec.id:04d}   #{asset} #Signal #{market} #{side.upper()}"
    body = (
        f"💎 Symbol : {asset}\n"
        f"📌 Type   : {market} / {side}\n"
        f"────────────────────────\n"
        f"💰 Entry  : {entry:g}\n"
        f"🛑 SL     : {sl:g}\n\n"
        f"🎯 TPs\n{_fmt_targets(tps, entry)}\n"
        f"────────────────────────\n"
    )
    if status.upper() == "CLOSED" and exitp is not None:
        body += f"✅ تم الإغلاق على: {exitp:g}\n"

    footer = (
        "\n(Disclaimer: Not financial advice. Manage your risk.)\n\n"
        "🔗 Crybto Radar Bot  |  📣 Official Channel  |  📬 Contact for subscription"
    )
    return f"{header}\n{body}{footer}"
# --- END OF FILE ---