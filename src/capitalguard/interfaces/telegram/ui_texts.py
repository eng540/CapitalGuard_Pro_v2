# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional

def _pct(entry: float, target: float, side: str) -> float:
    if entry == 0:
        return 0.0
    side = (side or "").upper()
    if side == "LONG":
        return (target - entry) / entry * 100.0
    return (entry - target) / entry * 100.0

def _hashtags(asset: str, market: Optional[str], side: str) -> str:
    tags = [
        f"#{(asset or '').upper()}".strip(),
        "#Signal",
        f"#{(market or 'Futures').title()}",
        f"#{side.title()}",
    ]
    return " ".join(tags)

def _footer() -> str:
    # روابط اختيارية من الإعدادات (إذا موجودة)
    try:
        from capitalguard.config import settings
        bot = getattr(settings, "TELEGRAM_BOT_USERNAME", None)
        ch  = getattr(settings, "TELEGRAM_CHANNEL_USERNAME", None)
        contact = getattr(settings, "TELEGRAM_CONTACT_USERNAME", None)
    except Exception:
        bot = ch = contact = None

    parts = []
    if bot:     parts.append(f"🤖 <a href=\"https://t.me/{bot}\">Bot</a>")
    if ch:      parts.append(f"📣 <a href=\"https://t.me/{ch}\">Official Channel</a>")
    if contact: parts.append(f"📬 <a href=\"https://t.me/{contact}\">Contact</a>")
    if not parts:
        return "🔗 Crybto Radar Bot  |  📣 Official Channel  |  📬 Contact for subscription"
    return "  |  ".join(parts)

@dataclass
class RecCard:
    id: int
    asset: str
    side: str
    entry: float
    stop_loss: float
    targets: List[float]
    status: str = "OPEN"
    market: Optional[str] = None
    notes: Optional[str] = None
    exit_price: Optional[float] = None

    def to_text(self) -> str:
        tags = _hashtags(self.asset, self.market, self.side)
        header = (
            "┌────────────────────────┐\n"
            f"│ 📣 <b>Trade Signal</b> — <code>#REC{self.id:04d}</code> │  {tags}\n"
            "└────────────────────────┘"
        )
        tp_lines = []
        for i, tp in enumerate(self.targets, start=1):
            pct = _pct(self.entry, tp, self.side)
            tp_lines.append(f"• TP{i}: <code>{tp:g}</code> (<code>{pct:+.2f}%</code>)")
        tps = "\n".join(tp_lines) if tp_lines else "-"

        rr = "-"  # يمكن حسابه لاحقًا إن رغبت (اختياري)
        notes = (self.notes or "").strip()
        notes_line = f"\n📝 Notes : {notes}\n" if notes else ""

        exit_line = f"\n\n✅ Closed @ <code>{self.exit_price:g}</code>" if (self.status.upper() == "CLOSED" and self.exit_price is not None) else ""

        body = (
            f"{header}\n"
            f"💎 <b>Symbol</b> : <code>{self.asset}</code>\n"
            f"📌 <b>Type</b>   : {(self.market or 'Futures').title()} / {self.side.upper()}\n"
            "────────────────────────\n"
            f"💰 <b>Entry</b>  : <code>{self.entry:g}</code>\n"
            f"🛑 <b>SL</b>     : <code>{self.stop_loss:g}</code>\n\n"
            f"🎯 <b>TPs</b>\n{tps}\n\n"
            "────────────────────────\n"
            f"📊 <b>R/R</b>   : {rr}"
            f"{notes_line}\n"
            "(Disclaimer: Not financial advice. Manage your risk.)"
            f"{exit_line}\n\n"
            f"{_footer()}"
        )
        return body

def build_trade_card_text(rec) -> str:
    """يبني نص بطاقة القناة من Recommendation."""
    asset = getattr(getattr(rec, "asset", None), "value", getattr(rec, "asset", ""))
    side  = getattr(getattr(rec, "side", None),  "value", getattr(rec, "side",  "LONG"))
    entry = getattr(getattr(rec, "entry", None), "value", getattr(rec, "entry", 0.0)) or 0.0
    sl    = getattr(getattr(rec, "stop_loss", None), "value", getattr(rec, "stop_loss", 0.0)) or 0.0
    targets = getattr(getattr(rec, "targets", None), "values", getattr(rec, "targets", [])) or []
    status  = getattr(rec, "status", "OPEN")
    market  = getattr(rec, "market", None)
    notes   = getattr(rec, "notes", None)
    exit_p  = getattr(rec, "exit_price", None)

    card = RecCard(
        id=int(getattr(rec, "id", 0)),
        asset=str(asset),
        side=str(side),
        entry=float(entry),
        stop_loss=float(sl),
        targets=[float(t) for t in targets],
        status=str(status),
        market=(market or None),
        notes=(notes or None),
        exit_price=exit_p if exit_p is not None else None,
    )
    return card.to_text()
# --- END OF FILE ---