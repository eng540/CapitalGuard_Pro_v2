# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Optional

@dataclass
class RecCard:
    id: int
    asset: str
    side: str            # LONG/SHORT
    status: str          # OPEN/CLOSED
    entry: float
    stop_loss: float
    targets: list[float]
    exit_price: Optional[float] = None
    market: Optional[str] = None   # Spot/Futures
    notes: Optional[str] = None

    def _targets_lines(self) -> str:
        lines = []
        base = self.entry
        # تجنب القسمة على صفر
        for i, tp in enumerate(self.targets, start=1):
            try:
                pct = ((tp - base) / base) * 100 if base else 0.0
            except Exception:
                pct = 0.0
            lines.append(f"• TP{i}: {tp:g} ({pct:+.2f}%)")
        return "\n".join(lines) if lines else "—"

    def to_text(self) -> str:
        """
        نص بطاقة مختصر لرسائل الإدارة داخل الخاص.
        """
        tps = " • ".join(f"{v:g}" for v in self.targets) if self.targets else "—"
        exitp = f"{self.exit_price:g}" if self.exit_price is not None else "-"
        mk = f"{self.market}/ " if self.market else ""
        return (
            f"🟢 <b>#{self.id}</b> — <b>{self.asset}</b> 📈\n"
            f"• الحالة: <b>{self.status}</b>\n"
            f"• {mk}الاتجاه: {self.side}\n"
            f"• الدخول: <code>{self.entry:g}</code>\n"
            f"• وقف الخسارة: <code>{self.stop_loss:g}</code>\n"
            f"• الأهداف: <code>{tps}</code>\n"
            f"• الخروج: <code>{exitp}</code>"
        )

def build_trade_card_text(rec) -> str:
    """
    نص البطاقة المخصص للنشر في القناة العامة — غني ومهيكل.
    يقبل كائن Recommendation (أو مماثل له في الخصائص المستخدمة).
    """
    # محاولاتًا لقراءة الحقول حتى لو كانت Enums/ValueObjects
    def _val(obj, name, default=None):
        v = getattr(obj, name, default)
        return getattr(v, "value", v)

    rid   = _val(rec, "id", "?")
    asset = _val(rec, "asset", "")
    side  = str(_val(rec, "side", "")).upper()
    market= _val(rec, "market", None)
    entry = float(_val(rec, "entry", 0))
    sl    = float(_val(rec, "stop_loss", 0))
    tps_v = _val(rec, "targets", []) or []
    notes = _val(rec, "notes", None)

    header_tags = " ".join(filter(None, [
        f"#{asset}",
        "#Signal",
        f"#{market}" if market else None,
        f"#{side.title()}" if side else None,
    ]))

    # بناء قائمة الأهداف مع النسب
    tps_lines = []
    for i, tp in enumerate(tps_v, start=1):
        try:
            pct = ((float(tp) - entry) / entry) * 100 if entry else 0.0
        except Exception:
            pct = 0.0
        tps_lines.append(f"• TP{i}: {float(tp):g} ({pct:+.2f}%)")
    tps_block = "\n".join(tps_lines) if tps_lines else "—"

    rr = "-"
    disclaimer = "(Disclaimer: Not financial advice. Manage your risk.)"
    notes_line = f"📝 Notes : {notes}\n" if notes else ""

    return (
        "┌────────────────────────┐\n"
        f"│ 📣 Trade Signal — #REC{int(rid):04d} │  {header_tags}\n"
        "└────────────────────────┘\n"
        f"💎 Symbol : {asset}\n"
        f"📌 Type   : {market or 'Spot'}/{side or '-'}\n"
        "────────────────────────\n"
        f"💰 Entry  : {entry:g}\n"
        f"🛑 SL     : {sl:g}\n\n"
        "🎯 TPs\n"
        f"{tps_block}\n"
        "────────────────────────\n"
        f"📊 R/R   : {rr}\n"
        f"{notes_line}"
        f"{disclaimer}"
    )