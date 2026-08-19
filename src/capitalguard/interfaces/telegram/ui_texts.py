# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v17.1.0-CLEAN-CARD (Leverage & Redundancy Fix)
# ✅ CRITICAL FIXES:
#    1. LEVERAGE FIX: Removed default '20x'. Only shows if explicitly set in notes (e.g., "Lev: 50x").
#    2. CLEAN LAYOUT: Removed redundant price/symbol info to save space.
#    3. BETTER UX: Improved Risk/Reward display formatting.

from __future__ import annotations
import logging
import re
from typing import Optional, Dict, Any
from decimal import Decimal
from datetime import datetime

# --- ✅ FIXED: Added Missing Telegram Imports ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

# --- Internal Imports ---
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.interfaces.telegram.helpers import _get_attr, _to_decimal, _pct

log = logging.getLogger(__name__)

# --- Configuration ---
WEBAPP_SHORT_NAME = "terminal"

# --- Helpers ---
def _get_webapp_link(rec_id: int, bot_username: str) -> str:
    try:
        safe_username = bot_username.replace("@", "") if bot_username else "CapitalGuardBot"
        return f"https://t.me/{safe_username}/{WEBAPP_SHORT_NAME}?startapp={rec_id}"
    except: return "https://t.me/CapitalGuardBot"

def _format_pnl_display(pnl: float) -> str:
    if pnl > 0: return f"💚 +{pnl:.2f}%"
    if pnl < 0: return f"🔻 {pnl:.2f}%"
    return "⚪ 0.00%"

def _format_price_clean(price) -> str:
    try:
        num = float(price)
        if num >= 1000: return f"{num:,.2f}"
        if num >= 1: return f"{num:.3f}"
        return f"{num:.5f}" # More precision for small assets
    except: return str(price)

# ✅ FIX: Removed default '20x'. Returns empty string if not found.
def _extract_leverage_str(notes: str) -> str:
    if not notes: return ""
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return f" • <b>{match.group(1)}</b>" if match else ""


def _record_identity(rec: Any) -> tuple[str, str]:
    """Return a source badge plus scoped and global identifiers when available."""
    record_id = getattr(rec, "record_id", None) or getattr(rec, "id", 0)
    public_ref = getattr(rec, "public_ref", None)
    display_ref = getattr(rec, "display_ref", None)
    source_type = str(getattr(rec, "source_type", "ANALYST_RECOMMENDATION") or "ANALYST_RECOMMENDATION")
    if source_type == "DIRECT_INPUT":
        label = "📝 <b>Trader Log</b>"
        legacy = f"UserTrade #{record_id}"
    elif source_type == "TRACKED_RECOMMENDATION":
        label = "📡 <b>Tracked Signal</b>"
        legacy = f"UserTrade #{record_id}"
    else:
        label = "🧠 <b>Analyst Recommendation</b>"
        legacy = f"Recommendation #{record_id}"
    identity = display_ref or legacy
    if public_ref:
        identity = f"{identity} · {public_ref}"
    return label, f"🆔 <b>{identity}</b>"

def _get_target_icon(target_index: int, hit_targets: set, total_targets: int) -> str:
    if target_index in hit_targets: return "✅"
    next_unhit = None
    for i in range(1, total_targets + 1):
        if i not in hit_targets:
            next_unhit = i
            break
    return "🎯" if target_index == next_unhit else "⏳"

def _calculate_duration(rec: Recommendation) -> str:
    try:
        if not rec.created_at or not rec.closed_at: return ""
        diff = rec.closed_at - rec.created_at
        
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        
        if days > 0: return f"({days}d {hours}h)"
        if hours > 0: return f"({hours}h {minutes}m)"
        return f"({minutes}m)"
    except: return ""

# --- CORE: Live Price Integration ---
async def get_live_price(symbol: str, market: str = "Futures") -> Optional[float]:
    try:
        from capitalguard.infrastructure.core_engine import core_cache
        cache_key = f"price:{market.upper()}:{symbol}"
        price = await core_cache.get(cache_key)
        if price: return float(price)
        
        alt_market = "SPOT" if market == "Futures" else "Futures"
        alt_key = f"price:{alt_market}:{symbol}"
        price = await core_cache.get(alt_key)
        return float(price) if price else None
    except Exception: return None

# --- CORE: Real PnL Calculator ---
def calculate_real_pnl(rec: Recommendation) -> Dict[str, Any]:
    try:
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        side = _get_attr(rec.side, 'value', 'LONG')
        status = _get_attr(rec, 'status')
        status_value = getattr(status, 'value', status)
        
        partial_closes = []
        total_closed_pct = 0.0
        
        if rec.events:
            for event in rec.events:
                event_type = getattr(event, 'event_type', '')
                event_data = getattr(event, 'event_data', {}) or {}
                if "PARTIAL" in str(event_type):
                    close_price = event_data.get('price')
                    close_pct = event_data.get('amount', 0) or event_data.get('closed_percent', 0)
                    if close_price and close_pct > 0:
                        profit = _pct(entry, Decimal(str(close_price)), side)
                        partial_closes.append({'profit': profit, 'percentage': float(close_pct), 'price': float(close_price)})
                        total_closed_pct += float(close_pct)
        
        realized_pnl = sum(c['profit'] * c['percentage'] / 100 for c in partial_closes)
        is_closed = str(status_value) == RecommendationStatus.CLOSED.value
        weighted_exit_price = None
        final_pnl = realized_pnl
        
        if is_closed:
            stored_pnl = _get_attr(rec, 'final_pnl_percentage', None)
            exit_price = _to_decimal(_get_attr(rec, 'exit_price', 0))
            remaining_pct = 100.0 - total_closed_pct
            if stored_pnl is not None and not partial_closes:
                # UserTrade carries the authoritative PnL calculated at close.
                final_pnl = float(stored_pnl)
            elif remaining_pct > 0.1:
                remaining_pnl = _pct(entry, exit_price, side)
                final_pnl += (remaining_pnl * remaining_pct / 100)
            
            if partial_closes:
                weighted_sum = sum(c['price'] * c['percentage'] for c in partial_closes)
                if remaining_pct > 0.1: weighted_sum += float(exit_price) * remaining_pct
                weighted_exit_price = weighted_sum / 100
            else:
                weighted_exit_price = float(exit_price)
        
        return {
            'total_pnl': round(final_pnl, 2),
            'realized_pnl': round(realized_pnl, 2),
            'closed_percentage': round(total_closed_pct, 2),
            'weighted_exit_price': weighted_exit_price
        }
    except Exception:
        return {'total_pnl': 0.0, 'realized_pnl': 0.0, 'closed_percentage': 0.0, 'weighted_exit_price': None}

# --- UI Constants ---
ICON_LONG = "🟢 LONG"
ICON_SHORT = "🔴 SHORT"

# --- PRO Card Builders ---
def _build_header(rec: Recommendation, bot_username: str) -> str:
    try:
        symbol = _get_attr(rec.asset, 'value', 'SYMBOL')
        side = _get_attr(rec.side, 'value', 'LONG')
        status = _get_attr(rec, 'status')
        notes = getattr(rec, 'notes', '')
        
        # ✅ FIX: Leverage only if present
        lev_str = _extract_leverage_str(notes)
        
        link = _get_webapp_link(getattr(rec, 'id', 0), bot_username)
        
        # Header Line 1: #SYMBOL • SIDE • LEVERAGE
        # Example: #BTCUSDT • LONG • 20x
        side_icon = "🟢" if side == "LONG" else "🔴"
        header_line = f"{side_icon} <a href='{link}'><b>#{symbol}</b></a> • {side}{lev_str}"
        
        return header_line
    except Exception:
        return "📊 <b>SIGNAL</b>"

def _build_status_dashboard(rec: Recommendation, is_initial_publish: bool = False) -> str:
    try:
        status = _get_attr(rec, 'status')
        live_price = getattr(rec, "live_price", None)
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        status_str = str(status.value if hasattr(status, 'value') else status)
        
        if status_str == "PENDING":
            txt = f"⏳ <b>PENDING</b>\nEntry: <code>{_format_price_clean(entry)}</code>"
            if live_price and live_price != float(entry):
                dist = _pct(entry, live_price, _get_attr(rec, 'side'))
                txt += f" (Diff: {abs(dist):.2f}%)"
            return txt
            
        if status_str == "CLOSED":
            pnl_data = calculate_real_pnl(rec)
            exit_price = _to_decimal(_get_attr(rec, 'exit_price', 0))
            duration = _calculate_duration(rec)
            dur_str = f" | ⏱️ {duration}" if duration else ""
            
            # ✅ FIX: Cleaner Closed View
            return (
                f"🏁 <b>TRADE CLOSED</b>\n"
                f"Result: <b>{_format_pnl_display(pnl_data['total_pnl'])}</b>{dur_str}\n"
                f"Exit: <code>{_format_price_clean(exit_price)}</code>"
            )

        if is_initial_publish:
            return "⚡ <b>ACTIVE</b>\nMarket Order Filled"
        
        current_price = live_price if live_price else float(entry)
        pnl = _pct(entry, current_price, _get_attr(rec, 'side', 'LONG'))
        
        # ✅ FIX: Simplified Live View
        return (
            f"🚀 <b>LIVE</b>\n"
            f"Price: <code>{_format_price_clean(current_price)}</code> ({_format_pnl_display(pnl)})"
        )
    except Exception:
        return "⚡ <b>ACTIVE</b>"

def _build_strategy_block(rec: Recommendation) -> str:
    try:
        entry = _format_price_clean(_get_attr(rec, 'entry', 0))
        sl = _format_price_clean(_get_attr(rec, 'stop_loss', 0))
        
        # Calculate Risk %
        e_val = _to_decimal(_get_attr(rec, 'entry', 0))
        s_val = _to_decimal(_get_attr(rec, 'stop_loss', 0))
        risk_pct = abs((e_val - s_val) / e_val * 100) if e_val > 0 else 0
        
        # ✅ FIX: Better formatting
        return (
            f"🚪 Entry: <code>{entry}</code>\n"
            f"🛑 Stop : <code>{sl}</code> (Risk: -{risk_pct:.2f}%)"
        )
    except Exception:
        return ""

def _build_targets_block(rec: Recommendation) -> str:
    try:
        targets = _get_attr(rec, 'targets', [])
        t_list = targets.values if hasattr(targets, 'values') else []
        if not t_list: return ""
        
        hit_targets = set()
        if rec.events:
            for e in rec.events:
                 if "TP" in str(getattr(e, 'event_type', '')) and "HIT" in str(getattr(e, 'event_type', '')):
                    try: hit_targets.add(int(''.join(filter(str.isdigit, e.event_type))))
                    except: pass
        
        lines = ["🎯 <b>TARGETS</b>"]
        for i, t in enumerate(t_list, 1):
            price = _get_attr(t, 'price', 0)
            icon = _get_target_icon(i, hit_targets, len(t_list))
            p_fmt = _format_price_clean(price)
            
            # Strikethrough if hit
            if i in hit_targets: p_fmt = f"<s>{p_fmt}</s>"
            
            close_pct = t.get('close_percent', 0) if isinstance(t, dict) else getattr(t, 'close_percent', 0)
            tag = f" ({int(close_pct)}%)" if close_pct > 0 else ""
            
            lines.append(f"{icon} TP{i}: {p_fmt}{tag}")
            
        return "\n".join(lines)
    except Exception: return ""

def _build_clean_timeline(rec: Recommendation) -> str:
    try:
        if not rec.events: return ""
        # Only show important events to keep card clean
        IMPORTANT = ["SL_HIT", "TP1_HIT", "TP2_HIT", "TP3_HIT", "TP4_HIT", "PARTIAL", "FINAL_CLOSE"]
        meaningful = [e for e in rec.events if getattr(e, 'event_type', '') in IMPORTANT]
        
        if not meaningful: return ""
        
        # Show last 3 events
        events = sorted(meaningful, key=lambda e: e.event_timestamp, reverse=True)[:3]
        
        lines = ["🕐 <b>Latest Updates:</b>"]
        for event in events:
            ts = getattr(event, 'event_timestamp', datetime.now()).strftime("%H:%M")
            e_type = getattr(event, 'event_type', '').replace("_", " ").title()
            event_data = getattr(event, 'event_data', {}) or {}
            mode = event_data.get('mode')
            mode_suffix = f" · {mode}" if mode else ""
            
            # Simplify event names
            if "Tp" in e_type: e_type = e_type.replace("Hit", "✅")
            elif "Sl" in e_type: e_type = "🛑 Stop Loss"
            elif "Partial" in e_type: e_type = "💰 Partial Exit"
            
            lines.append(f"▪️ `{ts}` {e_type}{mode_suffix}")
        return "\n".join(lines)
    except Exception: return ""

# --- MAIN BUILDER (Async) ---
async def build_trade_card_text(rec: Recommendation, bot_username: str, is_initial_publish: bool = False) -> str:
    try:
        symbol = _get_attr(rec.asset, 'value', 'SYMBOL')
        market = getattr(rec, 'market', 'Futures') or 'Futures'
        
        # Fetch Live Price
        cached_price = getattr(rec, 'live_price', None)
        if not cached_price:
            cached_price = await get_live_price(symbol, market)
        if not cached_price:
            cached_price = float(_to_decimal(_get_attr(rec, 'entry', 0)))
            
        setattr(rec, 'live_price', cached_price)
        
        DIVIDER = "────────────────"
        parts = []
        
        # 1. Header (Symbol + Side + Leverage)
        parts.append(_build_header(rec, bot_username))
        source_badge, record_label = _record_identity(rec)
        parts.append(f"{source_badge}  •  {record_label}")
        if getattr(rec, "source_type", "") == "TRACKED_RECOMMENDATION":
            source_ref = getattr(rec, "source_public_ref", None)
            analyst_code = getattr(rec, "source_analyst_code", None) or "Analyst"
            channel_code = getattr(rec, "channel_code", None) or "Channel"
            if source_ref:
                parts.append(
                    f"🔗 <b>Source Recommendation:</b> <code>{source_ref}</code> · {analyst_code} · {channel_code}"
                )
        parts.append("")
        
        # 2. Dashboard (Live Price + PnL)
        parts.append(_build_status_dashboard(rec, is_initial_publish))
        parts.append(DIVIDER)
        
        # 3. Strategy (Entry + SL + Risk)
        parts.append(_build_strategy_block(rec))
        parts.append("")
        
        # 4. Targets
        parts.append(_build_targets_block(rec))
        
        # 5. Notes (Cleaned)
        notes = getattr(rec, 'notes', '')
        if notes:
            # Remove technical notes like "Lev: 20x" from display notes
            clean_notes = re.sub(r'Lev:?\s*\d+x?\s*\|?', '', notes, flags=re.IGNORECASE).strip()
            if clean_notes:
                parts.append(DIVIDER)
                parts.append(f"📝 {clean_notes[:100]}")
        
        # 6. Timeline
        timeline = _build_clean_timeline(rec)
        if timeline:
            parts.append(DIVIDER)
            parts.append(timeline)
            
        # 7. Footer Link
        link = _get_webapp_link(getattr(rec, 'id', 0), bot_username)
        parts.append(f"\n🔍 <a href='{link}'><b>Open Analytics</b></a>")
        
        return "\n".join(parts)
    except Exception as e:
        log.error(f"Card Error: {e}", exc_info=True)
        return "📊 <b>SIGNAL ERROR</b>"

# --- Review Function ---
def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float] = None) -> str:
    asset = draft.get("asset", "SYMBOL")
    side = draft.get("side", "LONG")
    entry = _to_decimal(draft.get("entry", 0))
    sl = _to_decimal(draft.get("stop_loss", 0))
    icon = "🟢" if side == "LONG" else "🔴"
    
    text = (
        f"🛡️ <b>CONFIRM SIGNAL</b>\n\n"
        f"💎 <b>#{asset}</b>\n"
        f"Direction: {icon} <b>{side}</b>\n"
        f"Entry Price: <code>{_format_price_clean(entry)}</code>\n"
        f"Stop Loss: <code>{_format_price_clean(sl)}</code>\n"
    )
    
    if preview_price:
         text += f"Market Price: <code>{_format_price_clean(preview_price)}</code>\n"

    targets = draft.get("targets", [])
    if targets:
        text += f"\n🎯 <b>TARGETS:</b>\n"
        for i, target in enumerate(targets, 1):
            price = _to_decimal(target.get('price', 0))
            pct = target.get('close_percent', 0)
            tag = f" ({int(pct)}%)" if pct > 0 else ""
            text += f"TP{i}: <code>{_format_price_clean(price)}</code>{tag}\n"
    
    text += f"\n📤 <i>Ready to publish?</i>"
    return text

# --- PortfolioViews (Unchanged) ---
class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any],
                        active_count: int, watchlist_count: int, is_analyst: bool,
                        direct_active_count: int = 0, tracked_active_count: int = 0,
                        direct_watchlist_count: int = 0, tracked_watchlist_count: int = 0):
        try:
            from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace
            header = f"📊 <b>CapitalGuard Portfolio</b>\nWelcome, {user_name}."
            win_rate = report.get('win_rate_pct', 'N/A')
            total_pnl = report.get('total_pnl_pct', '0%')
            stats_card = (
                "────────────────\n"
                "📈 <b>Performance Summary</b>\n"
                f"• Win Rate: <b>{win_rate}</b>\n"
                f"• Total PnL: <b>{total_pnl}</b>\n"
                f"• Active Trades: <b>{active_count}</b>\n"
                "────────────────\n"
                "<b>Quick Access:</b>"
            )
            ns = CallbackNamespace.MGMT
            keyboard = [
                [InlineKeyboardButton(f"🚀 Active ({active_count})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
                [InlineKeyboardButton(f"👁️ Watchlist ({watchlist_count})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
                [InlineKeyboardButton(f"📝 My Logs ({direct_active_count + direct_watchlist_count})", callback_data=CallbackBuilder.create(ns, "show_list", "direct", 1))],
                [InlineKeyboardButton(f"📡 Tracked Signals ({tracked_active_count + tracked_watchlist_count})", callback_data=CallbackBuilder.create(ns, "show_list", "tracked", 1))],
                [InlineKeyboardButton("📜 History", callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))]
            ]
            if is_analyst:
                keyboard.append([InlineKeyboardButton("📈 Analyst Panel", callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])
            keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=CallbackBuilder.create(ns, "hub"))])
            text = f"{header}\n\n{stats_card}"
            if tracked_active_count + tracked_watchlist_count:
                text += (
                    "\n\n<i>📡 Tracked Signals = توصيات محلل متتبعة؛ "
                    "قد تكون حالتها Watchlist أو Active.</i>"
                )
            
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            else:
                await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        except BadRequest: pass
        except Exception as e: log.warning(f"Portfolio hub error: {e}")

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---