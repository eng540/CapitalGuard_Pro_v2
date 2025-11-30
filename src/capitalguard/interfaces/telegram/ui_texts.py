# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v3.0.0-GOLD (Config Fix)

from __future__ import annotations
import logging
import re
from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.interfaces.telegram.helpers import _get_attr, _to_decimal, _pct, _format_price
from capitalguard.config import settings

log = logging.getLogger(__name__)

# --- Configuration ---
# ⚠️ تأكد من أن هذا الاسم يطابق ما أنشأته في BotFather
WEBAPP_SHORT_NAME = "terminal" 
# ⚠️ اسم بوتك الحقيقي
BOT_USERNAME = "CapitalGuardProBot" 

# --- Helpers ---
def _get_webapp_link(rec_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}/{WEBAPP_SHORT_NAME}?startapp={rec_id}"

# --- Icons & Constants ---
ICON_LONG = "🟢"
ICON_SHORT = "🔴"
ICON_TP = "✅"
ICON_WAIT = "⏳"
ICON_STOP = "🛑"

def _format_pnl(pnl: float) -> str:
    emoji = "🚀" if pnl > 0 else "🔻"
    return f"{emoji} {pnl:+.2f}%"

def _extract_leverage(notes: str) -> str:
    if not notes: return "20x" 
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

def _rr(entry: Any, sl: Any, targets: List[Any]) -> str:
    try:
        e, s = _to_decimal(entry), _to_decimal(sl)
        if not targets: return "-"
        first_target = _to_decimal(_get_attr(targets[0], 'price'))
        risk = abs(e - s)
        if risk == 0: return "-"
        reward = abs(first_target - e)
        return f"1:{reward/risk:.1f}"
    except: return "-"

def _build_header(rec: Recommendation) -> str:
    symbol = _get_attr(rec.asset, 'value')
    side = _get_attr(rec.side, 'value')
    side_icon = ICON_LONG if side == "LONG" else ICON_SHORT
    
    raw_market = getattr(rec, 'market', 'Futures') or 'Futures'
    is_spot = "SPOT" in raw_market.upper()
    market_info = "💎 SPOT" if is_spot else f"⚡ FUTURES ({_extract_leverage(rec.notes)})"

    link = _get_webapp_link(rec.id)
    return f"<a href='{link}'>#{symbol}</a> | {side} {side_icon} | {market_info}"

def _build_status_and_live(rec: Recommendation) -> str:
    status = _get_attr(rec, 'status')
    live_price = getattr(rec, "live_price", None)
    
    if status == RecommendationStatus.PENDING:
        return f"⏳ **WAITING** | Live: `{_format_price(live_price) if live_price else '...'}`"
    
    if status == RecommendationStatus.CLOSED:
        exit_price = _format_price(_get_attr(rec, 'exit_price'))
        return f"🏁 **CLOSED** @ `{exit_price}`"

    if live_price:
        entry = _get_attr(rec, 'entry')
        pnl = _pct(entry, live_price, _get_attr(rec, 'side'))
        return f"⚡ **ACTIVE** | Live: `{_format_price(live_price)}`\nPnL: {_format_pnl(pnl)}"
    
    return "⚡ **ACTIVE** (Loading...)"

def _build_compact_entry_stop(rec: Recommendation) -> str:
    entry = _format_price(_get_attr(rec, 'entry'))
    sl = _format_price(_get_attr(rec, 'stop_loss'))
    targets = _get_attr(rec, 'targets', [])
    targets_list = targets.values if hasattr(targets, 'values') else []
    rr_str = _rr(_get_attr(rec, 'entry'), _get_attr(rec, 'stop_loss'), targets_list)

    return f"🚪 `{entry}` ➔ 🛑 `{sl}` | Risk: (R:R {rr_str})"

def _build_targets_list(rec: Recommendation) -> str:
    entry_price = _get_attr(rec, 'entry')
    targets = _get_attr(rec, 'targets', [])
    targets_list = targets.values if hasattr(targets, 'values') else []
    
    hit_targets = set()
    if rec.events:
        for event in rec.events:
            if "TP" in event.event_type and "HIT" in event.event_type:
                try: hit_targets.add(int(event.event_type[2:-4]))
                except: pass

    lines = []
    for i, target in enumerate(targets_list, start=1):
        price = _get_attr(target, 'price')
        pct_value = _pct(entry_price, price, _get_attr(rec, 'side'))
        icon = ICON_TP if i in hit_targets else ICON_WAIT
        lines.append(f"{icon} TP{i}: `{_format_price(price)}` `({pct_value:+.1f}%)`")
    
    return "\n".join(lines)

def build_trade_card_text(rec: Recommendation) -> str:
    SEP = "──────────────"
    parts = [
        _build_header(rec),
        _build_status_and_live(rec),
        SEP,
        _build_compact_entry_stop(rec),
        SEP,
        _build_targets_list(rec)
    ]
    
    if rec.events:
        last_event = sorted(rec.events, key=lambda e: e.event_timestamp, reverse=True)[0]
        ts = last_event.event_timestamp.strftime("%Y-%m-%d %H:%M")
        e_type = last_event.event_type.replace("_", " ").title()
        parts.append(SEP)
        parts.append(f"▫️ `{ts}` {e_type}")
    
    link = _get_webapp_link(rec.id)
    parts.append(f"\n📊 <a href='{link}'>Open Full Analytics</a>")

    return "\n".join(parts)

def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float]) -> str:
    # (نسخة مبسطة للمراجعة)
    asset = draft.get("asset", "N/A")
    side = draft.get("side", "N/A")
    market = draft.get("market", "Futures")
    entry = _to_decimal(draft.get("entry", 0))
    sl = _to_decimal(draft.get("stop_loss", 0))
    
    base_text = (
        f"📝 <b>REVIEW RECOMMENDATION</b>\n"
        f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        f"<b>#{asset} | {market} | {side}</b>\n\n"
        f"💰 Entry: <code>{_format_price(entry)}</code>\n"
        f"🛑 Stop: <code>{_format_price(sl)}</code>\n"
    )
    
    if preview_price is not None:
        base_text += f"\n💹 Current Price: <code>{_format_price(preview_price)}</code>"
    
    base_text += "\n\nReady to publish?"
    return base_text
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---