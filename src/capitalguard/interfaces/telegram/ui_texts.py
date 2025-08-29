# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List

from capitalguard.domain.entities import Recommendation


def _num(x) -> float:
    """يحاول استخراج رقم بسيط من ValueObject/Plain."""
    if x is None:
        return 0.0
    return float(getattr(x, "value", x))


def _as_list(targets) -> List[float]:
    """
    يحوّل Targets VO أو أي صيغة إلى قائمة أرقام.
    """
    if targets is None:
        return []
    if hasattr(targets, "values"):
        return [float(t) for t in list(getattr(targets, "values"))]
    if isinstance(targets, (list, tuple)):
        return [float(t) for t in targets]
    if isinstance(targets, str):
        parts = targets.replace(",", " ").split()
        return [float(p) for p in parts if p.strip()]
    if isinstance(targets, Iterable):
        return [float(t) for t in targets]  # type: ignore
    return []


def _pct(from_price: float, to_price: float, side: str) -> float:
    if from_price == 0:
        return 0.0
    side_up = (side or "").upper()
    if side_up == "SHORT":
        return (from_price - to_price) / from_price * 100.0
    return (to_price - from_price) / from_price * 100.0


def build_trade_card_text(rec: Recommendation) -> str:
    rid = getattr(rec, "id", 0) or 0
    symbol = str(getattr(getattr(rec, "asset", None), "value", getattr(rec, "asset", "")) or "").upper()
    side = str(getattr(getattr(rec, "side", None), "value", getattr(rec, "side", "")) or "").upper()
    market = (getattr(rec, "market", None) or "Futures").title()
    notes = getattr(rec, "notes", None) or "-"
    status = str(getattr(rec, "status", "OPEN")).upper()

    entry = _num(getattr(rec, "entry", 0))
    sl = _num(getattr(rec, "stop_loss", 0))
    tps = _as_list(getattr(rec, "targets", []))
    exit_price = getattr(rec, "exit_price", None)

    header = (
        "┌────────────────────────┐\n"
        f"│ 📣 Trade Signal — #REC{rid:04d} │  #{symbol} #Signal #{market} #{side}\n"
        "└────────────────────────┘"
    )

    base = (
        f"\n💎 Symbol : {symbol}\n"
        f"📌 Type   : {market} / {side}\n"
        "────────────────────────\n"
        f"💰 Entry  : {entry:g}\n"
        f"🛑 SL     : {sl:g}\n"
    )

    lines = []
    for i, tp in enumerate(tps, start=1):
        pct = _pct(entry, float(tp), side)
        lines.append(f"• TP{i}: {float(tp):g} ({pct:+.2f}%)")
    tps_block = "🎯 TPs\n" + ("\n".join(lines) if lines else "—")

    rr_block = (
        "\n\n────────────────────────\n"
        f"📊 R/R   : —\n"
        f"📝 Notes : {notes}\n\n"
        "(Disclaimer: Not financial advice. Manage your risk.)"
    )

    if status == "CLOSED" and exit_price is not None:
        rr_block += f"\n\n✅ Closed at: {exit_price:g}"

    footer = "\n\n🔗 Crybto Radar Bot  |  📣 Official Channel  |  📬 Contact for subscription"

    return header + base + tps_block + rr_block + footer
# --- END OF FILE ---