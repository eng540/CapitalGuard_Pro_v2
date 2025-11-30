# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v9.0.0-GOLD-MASTER (Visual Fixes + PortfolioViews)
# ✅ FEATURES:
#    1. Visual Hierarchy: Icons for Long/Short, Closed/Active.
#    2. Smart Targets: Shows 'Next' target (🚀) and 'Hit' (✅).
#    3. Close Percentages: Shows '📦 50%' next to targets.
#    4. PortfolioViews: Includes the missing class for /myportfolio.

from __future__ import annotations
import logging
import re
from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.error import BadRequest

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.interfaces.telegram.helpers import _get_attr, _to_decimal, _pct, _format_price
from capitalguard.config import settings

log = logging.getLogger(__name__)

# --- Configuration ---
WEBAPP_SHORT_NAME = "terminal"

# --- Helpers ---
def _get_webapp_link(rec_id: int, bot_username: str) -> str:
    """Generate Deep Link dynamically using the provided bot username."""
    try:
        safe_username = bot_username.replace("@", "") if bot_username else "CapitalGuardBot"
        return f"https://t.me/{safe_username}/{WEBAPP_SHORT_NAME}?startapp={rec_id}"
    except Exception as e:
        log.warning(f"WebApp link error: {e}")
        return f"https://t.me/CapitalGuardBot"

# --- Enhanced Icons ---
ICON_LONG = "🟢 LONG"
ICON_SHORT = "🔴 SHORT"
ICON_STOP = "🛑"
ICON_ENTRY = "🚪"
ICON_CLOSE = "💰"
# Smart Target Icons
ICON_HIT = "✅"
ICON_NEXT = "🚀"
ICON_WAIT = "⏳"

def _format_pnl(pnl: float) -> str:
    if pnl > 5: return f"🚀 +{pnl:.2f}%"
    if pnl > 0: return f"💚 +{pnl:.2f}%"
    if pnl < -5: return f"💀 {pnl:.2f}%"
    if pnl < 0: return f"🔻 {pnl:.2f}%"
    return "⚪ 0.00%"

def _extract_leverage(notes: str) -> str:
    if not notes: return "20x" 
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

def _draw_progress_bar(percent: float, length: int = 8) -> str:
    percent = max(0, min(100, percent))
    filled = int(length * percent // 100)
    return "█" * filled + "░" * (length - filled)

def _calculate_duration(rec: Recommendation) -> str:
    """Calculate trade duration"""
    try:
        if not rec.created_at or not rec.closed_at: 
            return ""
        diff = rec.closed_at - rec.created_at
        hours, remainder = divmod(diff.seconds, 3600)
        minutes = remainder // 60
        if diff.days > 0: 
            return f"{diff.days}d {hours}h"
        return f"{hours}h {minutes}m"
    except Exception:
        return ""

def _get_target_icon(target_index: int, hit_targets: set, total_targets: int) -> str:
    """Determine the correct icon for each target."""
    if target_index in hit_targets:
        return ICON_HIT
    
    next_unhit_target = None
    for i in range(1, total_targets + 1):
        if i not in hit_targets:
            next_unhit_target = i
            break
    
    if target_index == next_unhit_target:
        return ICON_NEXT
    else:
        return ICON_WAIT

# --- PRO Card Builders ---

def _build_header(rec: Recommendation, bot_username: str) -> str:
    """Build header with dynamic bot username and status icon."""
    try:
        symbol = _get_attr(rec.asset, 'value', 'SYMBOL')
        side = _get_attr(rec.side, 'value', 'LONG')
        status = _get_attr(rec, 'status')

        # Visual Status
        if status == RecommendationStatus.CLOSED:
            header_icon = "🏁"
            status_tag = " [CLOSED]"
        elif status == RecommendationStatus.PENDING:
            header_icon = "⏳"
            status_tag = ""
        else:
            header_icon = "📈" if side == "LONG" else "📉"
            status_tag = ""
        
        side_badge = ICON_LONG if side == "LONG" else ICON_SHORT
        
        raw_market = getattr(rec, 'market', 'Futures') or 'Futures'
        is_spot = "SPOT" in raw_market.upper()
        lev_info = "" if is_spot else f" • <b>{_extract_leverage(getattr(rec, 'notes', ''))}</b>"

        link = _get_webapp_link(getattr(rec, 'id', 0), bot_username)
        return f"{header_icon} <a href='{link}'><b>#{symbol}</b></a>{status_tag}\n{side_badge}{lev_info}"
    except Exception:
        return "📊 <b>TRADING SIGNAL</b>"

def _build_status_dashboard(rec: Recommendation, is_initial_publish: bool = False) -> str:
    """Enhanced status dashboard."""
    try:
        status = _get_attr(rec, 'status')
        live_price = getattr(rec, "live_price", None)
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        
        status_str = str(status.value if hasattr(status, 'value') else status)
        
        if status_str == "PENDING":
            txt = f"⏳ <b>PENDING ORDER</b>\nWait for Entry @ <code>{_format_price(entry)}</code>"
            if live_price:
                dist = _pct(entry, live_price, _get_attr(rec, 'side'))
                txt += f"\nCurrent: `{_format_price(live_price)}` ({abs(dist):.2f}% away)"
            return txt
            
        if status_str == "CLOSED":
            exit_price = _to_decimal(_get_attr(rec, 'exit_price', 0))
            pnl = _pct(entry, exit_price, _get_attr(rec, 'side', 'LONG'))
            duration = _calculate_duration(rec)
            dur_str = f" | ⏱️ {duration}" if duration else ""
            
            result_emoji = "🏆" if pnl > 0 else "📉"
            return (
                f"{result_emoji} <b>TRADE CLOSED</b>\n"
                f"Final Price: <code>{_format_price(exit_price)}</code>\n"
                f"Result: <b>{_format_pnl(pnl)}</b>{dur_str}"
            )

        # Initial Publish (Clean Look)
        if is_initial_publish:
            return "⚡ <b>TRADE ACTIVE</b>\nPosition opened successfully"
        
        # Active Update
        if live_price:
            pnl = _pct(entry, live_price, _get_attr(rec, 'side', 'LONG'))
            
            targets = _get_attr(rec, 'targets', [])
            t_vals = targets.values if hasattr(targets, 'values') else []
            
            if t_vals:
                first_tp = _to_decimal(_get_attr(t_vals[0], 'price', entry))
                goal_dist = abs(first_tp - entry)
                curr_dist = abs(live_price - entry)
                progress = min(100, (curr_dist / goal_dist * 100)) if goal_dist > 0 else 0
                bar = _draw_progress_bar(progress)
                
                return (
                    f"⚡ <b>LIVE TRADING</b>\n"
                    f"Current: <code>{_format_price(live_price)}</code> ({_format_pnl(pnl)})\n"
                    f"<code>{bar}</code>"
                )
            
            return f"⚡ <b>LIVE TRADING</b>\nCurrent: <code>{_format_price(live_price)}</code> ({_format_pnl(pnl)})"
        
        return "⚡ <b>TRADE ACTIVE</b>"
        
    except Exception:
        return "⚡ <b>TRADE ACTIVE</b>"

def _build_strategy_block(rec: Recommendation) -> str:
    """Strategy essentials."""
    try:
        entry = _format_price(_get_attr(rec, 'entry', 0))
        sl = _format_price(_get_attr(rec, 'stop_loss', 0))
        
        e_val = _to_decimal(_get_attr(rec, 'entry', 0))
        s_val = _to_decimal(_get_attr(rec, 'stop_loss', 0))
        risk_pct = abs((e_val - s_val) / e_val * 100) if e_val > 0 else 0
        
        return (
            f"{ICON_ENTRY} <b>Entry:</b> <code>{entry}</code>\n"
            f"{ICON_STOP} <b>Stop Loss:</b> <code>{sl}</code> ({risk_pct:.2f}% Risk)"
        )
    except Exception:
        return f"{ICON_ENTRY} <b>Entry:</b> <code>N/A</code>"

def _build_targets_block(rec: Recommendation) -> str:
    """Targets with smart icons and percentages."""
    try:
        entry_price = _get_attr(rec, 'entry', 0)
        targets = _get_attr(rec, 'targets', [])
        targets_list = targets.values if hasattr(targets, 'values') else []
        
        if not targets_list:
            return "🎯 <b>Take Profit Targets:</b> No targets set"
        
        hit_targets = set()
        if rec.events:
            for event in rec.events:
                event_type = getattr(event, 'event_type', '')
                if "TP" in event_type and "HIT" in event.event_type:
                    try:
                        target_num = int(''.join(filter(str.isdigit, event_type)))
                        hit_targets.add(target_num)
                    except: pass

        lines = ["🎯 <b>Targets:</b>"]
        
        for i, target in enumerate(targets_list, start=1):
            price = _get_attr(target, 'price', 0)
            pct_value = _pct(entry_price, price, _get_attr(rec, 'side', 'LONG'))
            icon = _get_target_icon(i, hit_targets, len(targets_list))
            
            # Close Percentage Display
            close_percent = 0.0
            if isinstance(target, dict):
                close_percent = float(target.get('close_percent', 0))
            elif hasattr(target, 'close_percent'):
                close_percent = float(target.close_percent)

            close_tag = ""
            if close_percent > 0:
                if close_percent == 100 and i == len(targets_list):
                    close_tag = " 🏁"
                else:
                    close_tag = f" 📦{int(close_percent)}%"
            
            # Strikethrough for hit targets
            if i in hit_targets:
                line = f"{icon} <s>TP{i}: {price:g}</s> (+{pct_value:.1f}%){close_tag}"
            else:
                line = f"{icon} TP{i}: <code>{price:g}</code> <i>(+{pct_value:.1f}%)</i>{close_tag}"
            
            lines.append(line)
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error(f"Error building targets: {e}")
        return "🎯 <b>Take Profit Targets:</b> Error loading targets"

def _build_clean_timeline(rec: Recommendation) -> str:
    """Clean timeline."""
    try:
        if not rec.events: return ""
        
        IGNORED_EVENTS = ["CREATED", "CREATED_ACTIVE", "CREATED_PENDING", "PUBLISHED"]
        meaningful_events = [e for e in rec.events if getattr(e, 'event_type', '') not in IGNORED_EVENTS]
        
        if not meaningful_events: return ""
            
        events_sorted = sorted(meaningful_events, key=lambda e: getattr(e, 'event_timestamp', datetime.now()), reverse=True)[:3]
        lines = ["🕐 <b>Recent Activity:</b>"]
        
        for event in events_sorted:
            ts = getattr(event, 'event_timestamp', datetime.now()).strftime("%H:%M")
            e_type = getattr(event, 'event_type', '').replace("_", " ").title()
            
            if "Tp" in e_type and "Hit" in e_type: e_type = "🎯 Target Hit"
            elif "Sl" in e_type and "Hit" in e_type: e_type = "🛑 Stop Loss"
            elif "Partial" in e_type: e_type = "💰 Partial Close"
            elif "Activated" in e_type: e_type = "⚡ Activated"
            elif "Closed" in e_type: e_type = "🏁 Closed"
                
            lines.append(f"▸ `{ts}` {e_type}")
        
        return "\n".join(lines)
    except Exception: return ""

def build_trade_card_text(rec: Recommendation, bot_username: str, is_initial_publish: bool = False) -> str:
    """Main card builder."""
    try:
        DIVIDER = "────────────────"
        parts = []
        
        parts.append(_build_header(rec, bot_username))
        parts.append("")
        parts.append(_build_status_dashboard(rec, is_initial_publish))
        parts.append(DIVIDER)
        parts.append(_build_strategy_block(rec))
        parts.append(DIVIDER)
        parts.append(_build_targets_block(rec))
        
        notes = getattr(rec, 'notes', '')
        if notes and len(notes.strip()) > 1:
            clean_notes = re.sub(r'Lev:?\s*\d+x?\s*\|?', '', notes, flags=re.IGNORECASE).strip()
            if clean_notes:
                parts.append(DIVIDER)
                parts.append(f"📝 <b>Notes:</b> {clean_notes[:100]}")
        
        timeline = _build_clean_timeline(rec)
        if timeline:
            parts.append(DIVIDER)
            parts.append(timeline)
        
        link = _get_webapp_link(getattr(rec, 'id', 0), bot_username)
        parts.append(f"\n🔍 <a href='{link}'><b>View Detailed Analytics & Control</b></a>")

        return "\n".join(parts)
    except Exception as e:
        log.error(f"Error building trade card: {e}")
        return "📊 <b>TRADING SIGNAL</b>"

def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float] = None) -> str:
    """Review text for analysts."""
    asset = draft.get("asset", "SYMBOL")
    side = draft.get("side", "LONG")
    entry = _to_decimal(draft.get("entry", 0))
    sl = _to_decimal(draft.get("stop_loss", 0))
    icon = "🟢" if side == "LONG" else "🔴"
    
    text = (
        f"🛡️ <b>Confirm Trading Signal</b>\n\n"
        f"💎 <b>#{asset}</b>\n"
        f"Direction: {icon} <b>{side}</b>\n"
        f"Entry Price: <code>{_format_price(entry)}</code>\n"
        f"Stop Loss: <code>{_format_price(sl)}</code>\n"
    )
    
    targets = draft.get("targets", [])
    if targets:
        text += f"\n🎯 <b>Targets:</b>\n"
        for i, target in enumerate(targets, start=1):
            price = _to_decimal(target.get('price', 0))
            pct = target.get('close_percent', 0)
            pct_str = f" 📦{int(pct)}%" if pct > 0 else ""
            text += f"TP{i}: <code>{_format_price(price)}</code>{pct_str}\n"
    
    text += f"\n📤 <i>Ready to publish?</i>"
    return text

# --- ✅ ADDED: PortfolioViews Class (Crucial for /myportfolio) ---
class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any], active_count: int, watchlist_count: int, is_analyst: bool):
        """Renders the main portfolio dashboard."""
        try:
            # Import locally to avoid circular import issues
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
                [InlineKeyboardButton("📜 History", callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))]
            ]
            
            if is_analyst:
                keyboard.append([InlineKeyboardButton("📈 Analyst Panel", callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])

            keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=CallbackBuilder.create(ns, "hub"))])

            text = f"{header}\n\n{stats_card}"
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text=text, 
                    reply_markup=InlineKeyboardMarkup(keyboard), 
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.effective_message.reply_text(
                    text=text, 
                    reply_markup=InlineKeyboardMarkup(keyboard), 
                    parse_mode=ParseMode.HTML
                )
        except BadRequest:
            pass
        except Exception as e:
            log.warning(f"Portfolio hub error: {e}")

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---