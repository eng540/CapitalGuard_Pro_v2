# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List, Optional

def _pct(entry: float, target: float, side: str) -> float:
    """يحسِب نسبة الهدف بالنسبة لسعر الدخول مع مراعاة الاتجاه."""
    if entry == 0:
        return 0.0
    side = (side or "").upper()
    if side == "LONG":
        return (target - entry) / entry * 100.0
    return (entry - target) / entry * 100.0  # SHORT

def _format_targets(entry: float, side: str, tps: Iterable[float]) -> str:
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        lines.append(f"• TP{i}: {tp:g} ({_pct(entry, float(tp), side):+.2f}%)")
    return "\n".join(lines) if lines else "—"

def build_trade_card_text(rec) -> str:
    """
    يبني نص بطاقة القناة (HTML) اعتمادًا على كائن Recommendation.
    الحقول المتوقعة في rec:
      id, asset(str|Symbol.value), side(str|Side.value), market(str), entry(Price.value|float),
      stop_loss(Price.value|float), targets(Targets.values|list), notes(str|None),
      status('OPEN'|'CLOSED'), exit_price(float|None)
    """
    rid = getattr(rec, "id", None)
    asset = str(getattr(rec.asset, "value", getattr(rec, "asset", ""))).upper()
    side = str(getattr(rec.side, "value", getattr(rec, "side", ""))).upper()
    market = str(getattr(rec, "market", "Futures")).title()
    entry = float(getattr(rec.entry, "value", getattr(rec, "entry", 0.0)))
    sl = float(getattr(rec.stop_loss, "value", getattr(rec, "stop_loss", 0.0)))
    tps = list(getattr(getattr(rec, "targets", []), "values", getattr(rec, "targets", [])) or [])
    notes: Optional[str] = getattr(rec, "notes", None)
    status = str(getattr(rec, "status", "OPEN")).upper()
    exit_price = getattr(rec, "exit_price", None)

    header = (
        f"📣 <b>Trade Signal</b> — <code>#REC{rid:04d}</code>  |  "
        f"<code>#{asset}</code> #Signal #{market.replace(' ', '')} #{side}\n"
    )
    body = (
        f"💎 <b>Symbol</b> : {asset}\n"
        f"📌 <b>Type</b>   : {market} / {side}\n"
        f"────────────────────────\n"
        f"💰 <b>Entry</b>  : {entry:g}\n"
        f"🛑 <b>SL</b>     : {sl:g}\n\n"
        f"🎯 <b>TPs</b>\n{_format_targets(entry, side, tps)}\n"
        f"────────────────────────\n"
        f"📊 <b>R/R</b>   : —\n"
        f"📝 <b>Notes</b> : {notes or '-'}\n\n"
        f"(Disclaimer: Not financial advice. Manage your risk.)\n"
    )

    footer = ""
    if status == "CLOSED":
        footer = f"\n✅ <b>Closed at</b>: {exit_price:g}"

    promo = "\n\n🔗 <b>Crybto Radar Bot</b>  |  📣 <b>Official Channel</b>  |  📬 <b>Contact for subscription</b>"

    return header + body + footer + promo

def build_review_text(draft: dict) -> str:
    """نص المراجعة داخل البوت قبل النشر."""
    asset = draft["asset"].upper()
    side = draft["side"].upper()
    market = draft["market"].title()
    entry = float(draft["entry"])
    sl = float(draft["stop_loss"])
    tps = draft["targets"]

    lines = "\n".join([f"• TP{i}: {tp:g}" for i, tp in enumerate(tps, start=1)])
    notes = draft.get("notes") or "-"

    return (
        "📝 <b>مراجعة التوصية</b>\n"
        f"<b>{asset}</b> 💎\n"
        f"{market} / {side} 📌\n"
        f"الدخول 💰: {entry:g}\n"
        f"ووقف الخسارة 🛑: {sl:g}\n"
        f"الأهداف 🎯:\n{lines}\n"
        f"ملاحظة 📝: {notes}\n"
        "\nهل تريد نشر هذه التوصية في القناة؟"
    )
# --- END OF FILE ---