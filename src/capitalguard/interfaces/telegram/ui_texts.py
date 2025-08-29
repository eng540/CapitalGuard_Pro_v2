# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable

WELCOME = (
    "👋 أهلاً بك في <b>CapitalGuard Bot</b>.\n"
    "ابدأ بـ <code>/newrec</code> لإنشاء توصية جديدة، أو <code>/open</code> لإدارتها."
)

HELP = (
    "<b>الأوامر المتاحة:</b>\n\n"
    "• <code>/newrec</code> — إنشاء توصية تفاعليًا\n"
    "• <code>/open</code> — عرض وإدارة التوصيات المفتوحة\n"
    "• <code>/list</code> — إحصاء سريع للتوصيات المفتوحة\n"
    "• <code>/analytics</code> — ملخص أداء\n"
    "• <code>/ping</code> — فحص اتصال"
)

def _fmt_targets(targets: Iterable[float], entry: float | None = None) -> str:
    parts = []
    for i, t in enumerate(targets, 1):
        if entry and entry != 0:
            pct = (t - entry) / entry * 100
            parts.append(f"• TP{i}: {t:g} ({pct:+.1f}%)")
        else:
            parts.append(f"• TP{i}: {t:g}")
    return "\n".join(parts) if parts else "—"

def build_trade_card_text(rec) -> str:
    """
    يبني بطاقة القناة النصية (بدون أزرار).
    يدعم حقول: id, asset, side, market, entry, stop_loss, targets, status, exit_price.
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