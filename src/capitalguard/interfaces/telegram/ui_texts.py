# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v66.1.0-HOTFIX (Restored Missing Function)
# ✅ THE FIX:
#    1. RESTORED: 'build_review_text_with_price' function (Critical for Creation Flow).
#    2. MAINTAINED: All previous design improvements (Compact Card, Timeline, Links).

from __future__ import annotations
import logging
import re
from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.domain.value_objects import Target
from capitalguard.interfaces.telegram.helpers import _get_attr, _to_decimal, _pct, _format_price
from capitalguard.config import settings 

log = logging.getLogger(__name__)

# --- Icons & Constants ---
ICON_LONG = "🟢"
ICON_SHORT = "🔴"
ICON_TP = "✅"
ICON_WAIT = "⏳"
ICON_STOP = "🛑"

# ✅ MUST MATCH BOTFATHER SETTINGS
WEBAPP_SHORT_NAME = "terminal" 
# ✅ YOUR BOT USERNAME
BOT_USERNAME = "Tradingplatformxbot"

def _format_pnl(pnl: float) -> str:
    emoji = "🚀" if pnl > 0 else "🔻"
    return f"{emoji} {pnl:+.2f}%"

def _extract_leverage(notes: str) -> str:
    if not notes: return "20x" 
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

def _calculate_duration(rec: Recommendation) -> str:
    if not rec.created_at or not rec.closed_at: return ""
    diff = rec.closed_at - rec.created_at
    hours, remainder = divmod(diff.seconds, 3600)
    minutes = remainder // 60
    if diff.days > 0: return f"{diff.days}d {hours}h"
    return f"{hours}h {minutes}m"

def _rr(entry: Any, sl: Any, targets: List[Target]) -> str:
    try:
        entry_dec, sl_dec = _to_decimal(entry), _to_decimal(sl)
        if not targets: return "-"
        first_target = targets[0]
        first_target_price = _to_decimal(_get_attr(first_target, 'price'))
        if not entry_dec.is_finite() or not sl_dec.is_finite() or not first_target_price.is_finite(): return "-"
        risk = abs(entry_dec - sl_dec)
        if risk.is_zero(): return "∞"
        reward = abs(first_target_price - entry_dec)
        ratio = reward / risk
        return f"1:{ratio:.1f}"
    except Exception: return "-"

def _get_webapp_link(rec_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}/{WEBAPP_SHORT_NAME}?startapp={rec_id}"

# --- ✅ RESTORED FUNCTION: Review Card for Creation Flow ---
def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float]) -> str:
    """
    Builds the text for the creation review card (Draft).
    Used by conversation_handlers.py.
    """
    asset = draft.get("asset", "N/A")
    side = draft.get("side", "N/A")
    market = draft.get("market", "Futures")
    entry = _to_decimal(draft.get("entry", 0))
    sl = _to_decimal(draft.get("stop_loss", 0))
    
    # Format Targets
    raw_tps = draft.get("targets", [])
    target_lines = []
    for i, t in enumerate(raw_tps, start=1):
        price = _to_decimal(t.get('price', 0))
        pct_value = _pct(entry, price, side)
        close_percent = t.get('close_percent', 0)
        suffix = f" (Close {close_percent:.0f}%)" if 0 < close_percent < 100 else ""
        if close_percent == 100 and i == len(raw_tps): suffix = ""
        
        target_lines.append(f"  • TP{i}: <code>{_format_price(price)}</code> ({_format_pnl(pct_value)}){suffix}")
    
    base_text = (
        f"📝 <b>REVIEW RECOMMENDATION</b>\n"
        f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        f"<b>#{asset} | {market} | {side}</b>\n\n"
        f"💰 Entry: <code>{_format_price(entry)}</code>\n"
        f"🛑 Stop: <code>{_format_price(sl)}</code>\n"
        f"🎯 Targets:\n" + "\n".join(target_lines) + "\n"
    )
    
    if preview_price is not None:
        base_text += f"\n💹 Current Price: <code>{_format_price(preview_price)}</code>"
    
    base_text += "\n\nReady to publish?"
    return base_text

# --- Standard Trade Card Builders ---

def _build_header(rec: Recommendation) -> str:
    symbol = _get_attr(rec.asset, 'value')
    side = _get_attr(rec.side, 'value')
    side_icon = ICON_LONG if side == "LONG" else ICON_SHORT
    
    raw_market = getattr(rec, 'market', 'Futures') or 'Futures'
    is_spot = "SPOT" in raw_market.upper()

    if is_spot:
        market_info = "💎 SPOT"
    else:
        lev_val = _extract_leverage(rec.notes)
        market_info = f"⚡ FUTURES ({lev_val})"

    link = _get_webapp_link(rec.id)
    return f"<a href='{link}'>#{symbol}</a> | {side} {side_icon} | {market_info}"

def _build_status_and_live(rec: Recommendation) -> str:
    status = _get_attr(rec, 'status')
    live_price = getattr(rec, "live_price", None)
    
    if status == RecommendationStatus.PENDING:
        return f"⏳ **WAITING** | Live: `{_format_price(live_price) if live_price else '...'}`"
    
    if status == RecommendationStatus.CLOSED:
        pnl = _calculate_weighted_pnl(rec)
        duration = _calculate_duration(rec)
        dur_str = f" | ⏱️ {duration}" if duration else ""
        exit_price = _format_price(_get_attr(rec, 'exit_price'))
        return f"🏁 **CLOSED** @ `{exit_price}`\nPnL: {_format_pnl(pnl)}{dur_str}"

    if live_price:
        entry = _get_attr(rec, 'entry')
        pnl = _pct(entry, live_price, _get_attr(rec, 'side'))
        return f"⚡ **ACTIVE** | Live: `{_format_price(live_price)}`\nPnL: {_format_pnl(pnl)}"
    
    return "⚡ **ACTIVE** (Loading...)"

def _build_compact_entry_stop(rec: Recommendation) -> str:
    entry = _format_price(_get_attr(rec, 'entry'))
    sl = _format_price(_get_attr(rec, 'stop_loss'))
    
    try:
        e_val = _to_decimal(_get_attr(rec, 'entry'))
        s_val = _to_decimal(_get_attr(rec, 'stop_loss'))
        risk_pct = abs((e_val - s_val) / e_val) * 100
        risk_str = f"{risk_pct:.1f}%"
    except: risk_str = "-"
    
    targets = _get_attr(rec, 'targets', [])
    targets_list = targets.values if hasattr(targets, 'values') else []
    rr_str = _rr(e_val, s_val, targets_list)

    return f"🚪 `{entry}` ➔ 🛑 `{sl}` | Risk: {risk_str} (R:R {rr_str})"

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
        line = f"{icon} TP{i}: `{_format_price(price)}` `({pct_value:+.1f}%)`"
        lines.append(line)
    
    return "\n".join(lines)

def _build_timeline_compact(rec: Recommendation) -> str:
    if not rec.events: return ""
    events = sorted(rec.events, key=lambda e: e.event_timestamp, reverse=True)[:3]
    lines = []
    for event in events:
        ts = event.event_timestamp.strftime("%Y-%m-%d %H:%M")
        e_type = event.event_type.replace("_", " ").title()
        
        if "Tp" in e_type and "Hit" in e_type: e_type = e_type.replace("Hit", "✅")
        if "Sl" in e_type: e_type = "SL Hit 🛑"
        if "Created" in e_type: e_type = "Published 📡"
        
        lines.append(f"▫️ `{ts}` {e_type}")
    return "\n".join(lines)

def build_trade_card_text(rec: Recommendation) -> str:
    SEP = "──────────────"
    parts = []
    
    parts.append(_build_header(rec))
    parts.append(_build_status_and_live(rec))
    parts.append(SEP)
    parts.append(_build_compact_entry_stop(rec))
    parts.append(SEP)
    parts.append(_build_targets_list(rec))
    
    if rec.notes:
        clean_notes = re.sub(r'Lev:?\s*\d+x?\s*\|?', '', rec.notes, flags=re.IGNORECASE).strip()
        if clean_notes:
            parts.append(SEP)
            parts.append(f"📝 {clean_notes}")
    
    timeline = _build_timeline_compact(rec)
    if timeline:
        parts.append(SEP)
        parts.append(timeline)
    
    link = _get_webapp_link(rec.id)
    parts.append(f"\n📊 <a href='{link}'>Open Full Analytics</a>")

    return "\n".join(parts)

# --- Helpers for PnL Calculation ---
def _calculate_weighted_pnl(rec: Recommendation) -> float:
    total_pnl_contribution = 0.0
    total_percent_closed = 0.0
    closure_event_types = ("PARTIAL_CLOSE_MANUAL", "PARTIAL_CLOSE_AUTO", "FINAL_CLOSE")

    if not rec.events:
        if rec.status == RecommendationStatus.CLOSED and rec.exit_price is not None:
            return _pct(rec.entry.value, rec.exit_price, rec.side.value)
        return 0.0

    for event in rec.events:
        event_type = getattr(event, "event_type", "")
        if event_type in closure_event_types:
            data = getattr(event, "event_data", {}) or {}
            closed_pct = data.get('closed_percent', 0.0)
            pnl_on_part = data.get('pnl_on_part', 0.0)
            if closed_pct > 0:
                total_pnl_contribution += (closed_pct / 100.0) * pnl_on_part
                total_percent_closed += closed_pct

    if total_percent_closed == 0 and rec.status == RecommendationStatus.CLOSED and rec.exit_price is not None:
        return _pct(rec.entry.value, rec.exit_price, rec.side.value)
         
    if 99.9 < total_percent_closed < 100.1:
        normalization_factor = 100.0 / total_percent_closed if total_percent_closed > 0 else 1.0
        return total_pnl_contribution * normalization_factor

    return total_pnl_contribution

class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any], active_count: int, watchlist_count: int, is_analyst: bool):
        from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace, CallbackAction
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        header = "📊 *CapitalGuard — My Portfolio*\nمنطقة التحكم الذكية."
        stats_card = (
            "──────────────\n"
            "📈 *الأداء العام (Activated)*\n"
            f" • الصفقات: `{report.get('total_trades', '0')}`\n"
            f" • صافي PnL: `{report.get('total_pnl_pct', 'N/A')}`\n"
            f" • نسبة النجاح: `{report.get('win_rate_pct', 'N/A')}`\n" 
            "──────────────\n"
            "*القوائم:*"
        )
        
        ns = CallbackNamespace.MGMT
        keyboard = [
            [InlineKeyboardButton(f"🚀 Active ({active_count})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
            [InlineKeyboardButton(f"👁️ Watchlist ({watchlist_count})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
            [InlineKeyboardButton("📡 Channels", callback_data=CallbackBuilder.create(ns, "show_list", "channels", 1))],
        ]
        
        if is_analyst:
            keyboard.append([InlineKeyboardButton("📈 Analyst Panel", callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])

        keyboard.append([InlineKeyboardButton("🔄 Refresh Data", callback_data=CallbackBuilder.create(ns, "hub"))])

        text = f"{header}\n\n{stats_card}"
        
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            else:
                await update.effective_message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        except BadRequest: pass
        except Exception as e: log.warning(f"Hub render error: {e}")
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---