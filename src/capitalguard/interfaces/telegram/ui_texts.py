# --- START OF HYBRID VERSION: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v11.2.0-HYBRID (Visual + Always Visible + Timeline)
# ✅ CORE FEATURES FROM ALL VERSIONS:
#     1. From v11.0.0: Strong Visual Identity + Marketing Focus + Risk Calculation
#     2. From v11.1.0: Always Show Price + Clean Structure + Guaranteed Display
#     3. From v9.0.0:  Timeline Activity + Progress Bars + Smart Target Icons + Spot Market Handling

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

log = logging.getLogger(__name__)

# --- Configuration ---
WEBAPP_SHORT_NAME = "terminal"
APP_NAME = "CapitalGuard"

# --- CORE: Live Price Integration (from v11.0.0 & v11.1.0) ---
async def get_live_price(symbol: str, market: str = "Futures") -> Optional[float]:
    """Get real-time price from Redis cache with fallback"""
    try:
        from capitalguard.infrastructure.core_engine import core_cache
        
        cache_key = f"price:{market.upper()}:{symbol}"
        price = await core_cache.get(cache_key)
        
        if price:
            return float(price)
        
        alt_market = "SPOT" if market == "Futures" else "Futures"
        alt_key = f"price:{alt_market}:{symbol}"
        price = await core_cache.get(alt_key)
        
        return float(price) if price else None
        
    except Exception as e:
        log.debug(f"Live price fetch failed for {symbol}: {e}")
        return None

# --- CORE: Real PnL Calculator (Enhanced from v11.0.0) ---
def calculate_real_pnl(rec: Recommendation) -> Dict[str, Any]:
    """
    Calculates TRUE PnL considering ALL partial closes
    Returns weighted average exit price and accurate profit/loss
    """
    try:
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        side = _get_attr(rec.side, 'value', 'LONG')
        status = _get_attr(rec, 'status')
        
        # Track partial closes from events (from v11.0.0)
        partial_closes = []
        total_closed_pct = 0.0
        
        if rec.events:
            for event in rec.events:
                event_type = getattr(event, 'event_type', '')
                event_data = getattr(event, 'event_data', {}) or {}
                
                # Partial close at target
                if "PARTIAL_CLOSE" in event_type:
                    close_price = event_data.get('price')
                    close_pct = event_data.get('closed_percent', 0)
                    
                    if close_price and close_pct > 0:
                        profit_at_close = _pct(entry, Decimal(str(close_price)), side)
                        partial_closes.append({
                            'price': float(close_price),
                            'percentage': float(close_pct),
                            'profit': profit_at_close
                        })
                        total_closed_pct += float(close_pct)
        
        # Calculate realized PnL from partial closes
        realized_pnl = sum(c['profit'] * c['percentage'] / 100 for c in partial_closes)
        
        # Final state
        is_closed = (status == RecommendationStatus.CLOSED)
        weighted_exit_price = None
        final_pnl = realized_pnl
        
        if is_closed:
            # Closed trade - calculate weighted exit price
            exit_price = _to_decimal(_get_attr(rec, 'exit_price', 0))
            remaining_pct = 100.0 - total_closed_pct
            
            if remaining_pct > 0:
                remaining_pnl = _pct(entry, exit_price, side)
                final_pnl += (remaining_pnl * remaining_pct / 100)
            
            # Calculate weighted average exit price
            if partial_closes:
                weighted_price = sum(c['price'] * c['percentage'] for c in partial_closes)
                if remaining_pct > 0:
                    weighted_price += float(exit_price) * remaining_pct
                weighted_exit_price = weighted_price / 100
            else:
                weighted_exit_price = float(exit_price)
        
        return {
            'total_pnl': round(final_pnl, 2),
            'realized_pnl': round(realized_pnl, 2),
            'closed_percentage': round(total_closed_pct, 2),
            'weighted_exit_price': weighted_exit_price,
            'is_closed': is_closed,
            'partial_closes_count': len(partial_closes)
        }
        
    except Exception as e:
        log.error(f"Real PnL calculation error: {e}")
        return {
            'total_pnl': 0.0,
            'realized_pnl': 0.0,
            'closed_percentage': 0.0,
            'weighted_exit_price': None,
            'is_closed': False,
            'partial_closes_count': 0
        }

# --- VISUAL IDENTITY: Enhanced from v11.0.0 with v9.0.0 improvements ---
def _get_visual_identity(status: RecommendationStatus, pnl: float = 0) -> tuple:
    """
    Returns (icon, color_tag, status_text) for strong visual identity
    Enhanced with v9.0.0's clearer status labels
    """
    if status == RecommendationStatus.PENDING:
        return ("⏳", "", "PENDING ORDER")
    
    elif status == RecommendationStatus.CLOSED:
        if pnl > 0:
            return ("🏆", "🟢", "CLOSED - WINNER")
        else:
            return ("💎", "🔴", "CLOSED - STOP LOSS")
    
    else:  # ACTIVE
        return ("🚀", "🔵", "LIVE TRADING")

def _format_pnl_display(pnl: float, realized: float = 0) -> str:
    """Clean PnL formatting with marketing focus (from v11.0.0)"""
    if pnl > 15: return f"🎯 +{pnl:.2f}%"
    if pnl > 8: return f"💰 +{pnl:.2f}%"
    if pnl > 0: return f"💚 +{pnl:.2f}%"
    if pnl < -15: return f"📉 {pnl:.2f}%"
    if pnl < -8: return f"⚫ {pnl:.2f}%"
    if pnl < 0: return f"🔸 {pnl:.2f}%"
    return "⚪ 0.00%"

def _format_price_clean(price) -> str:
    """Clean price formatting (from v11.0.0 with $)"""
    try:
        num = float(price)
        if num >= 1000:
            return f"${num:,.0f}"
        elif num >= 1:
            return f"${num:.2f}"
        else:
            return f"${num:.4f}"
    except:
        return str(price)

def _extract_leverage(notes: str, market: str = "Futures") -> str:
    """Extract leverage with Spot market handling (from v9.0.0)"""
    if "SPOT" in market.upper():
        return "1x (Spot)"
    
    if not notes:
        return "20x"
    
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

# --- FROM v9.0.0: Progress Bar & Timeline Functions ---
def _draw_progress_bar(percent: float, length: int = 8) -> str:
    """Create visual progress bar (from v9.0.0)"""
    percent = max(0, min(100, percent))
    filled = int(length * percent // 100)
    return "█" * filled + "░" * (length - filled)

def _build_clean_timeline(rec: Recommendation) -> str:
    """Build clean timeline of key events (from v9.0.0)"""
    if not rec.events:
        return ""
    
    IGNORED_EVENTS = ["CREATED", "CREATED_ACTIVE", "CREATED_PENDING", "PUBLISHED"]
    meaningful_events = [e for e in rec.events if getattr(e, 'event_type', '') not in IGNORED_EVENTS]
    
    if not meaningful_events:
        return ""
    
    # Sort and take latest 3 events
    events_sorted = sorted(meaningful_events, 
                          key=lambda e: getattr(e, 'event_timestamp', datetime.now()), 
                          reverse=True)[:3]
    
    lines = ["🕐 <b>Recent Activity:</b>"]
    
    for event in events_sorted:
        ts = getattr(event, 'event_timestamp', datetime.now()).strftime("%H:%M")
        e_type = getattr(event, 'event_type', '').replace("_", " ").title()
        
        # Humanize event types
        if "Tp" in e_type and "Hit" in e_type:
            e_type = "🎯 Target Hit"
        elif "Sl" in e_type and "Hit" in e_type:
            e_type = "🛑 Stop Loss"
        elif "Partial" in e_type:
            e_type = "💰 Partial Close"
        elif "Activated" in e_type:
            e_type = "⚡ Activated"
        elif "Closed" in e_type:
            e_type = "🏁 Closed"
        
        lines.append(f"▸ `{ts}` {e_type}")
    
    return "\n".join(lines)

def _get_target_icon(target_index: int, hit_targets: set, total_targets: int) -> str:
    """Smart target icon selection (from v9.0.0)"""
    if target_index in hit_targets:
        return "✅"
    
    # Find next unhit target
    next_unhit_target = None
    for i in range(1, total_targets + 1):
        if i not in hit_targets:
            next_unhit_target = i
            break
    
    if target_index == next_unhit_target:
        return "🚀"
    else:
        return "⏳"

# --- MAIN CARD BUILDER: Hybrid Approach ---
async def build_trade_card_text(rec: Recommendation, bot_username: str, is_initial_publish: bool = False) -> str:
    """
    HYBRID Trade Card: Best features from all versions
    1. Always shows price (v11.1.0)
    2. Strong visual identity (v11.0.0)
    3. Timeline and progress bars (v9.0.0)
    4. Smart target icons (v9.0.0)
    """
    try:
        # Basic data
        symbol = _get_attr(rec.asset, 'value', 'SYMBOL').upper()
        side = _get_attr(rec.side, 'value', 'LONG')
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        stop_loss = _to_decimal(_get_attr(rec, 'stop_loss', 0))
        status = _get_attr(rec, 'status')
        market = getattr(rec, 'market', 'Futures') or 'Futures'
        
        # ✅ ALWAYS GET PRICE (from v11.1.0)
        live_price = await get_live_price(symbol, market)
        display_price = live_price if live_price is not None else float(entry)
        
        # Real PnL calculation
        pnl_data = calculate_real_pnl(rec)
        
        # Visual identity
        icon, color_tag, status_text = _get_visual_identity(status, pnl_data['total_pnl'])
        
        # Header with ALWAYS VISIBLE PRICE
        side_badge = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        leverage = _extract_leverage(getattr(rec, 'notes', ''), market)
        
        # Price always in header (v11.1.0 improvement)
        price_display = f" • {_format_price_clean(display_price)}"
        
        safe_username = bot_username.replace("@", "")
        link = f"https://t.me/{safe_username}/{WEBAPP_SHORT_NAME}?startapp={rec.id}"
        
        # Start building
        lines = []
        DIVIDER = "────────────────"
        
        # Line 1: Visual Identity + Price (ALWAYS VISIBLE)
        lines.append(f"{icon} <b>#{symbol}</b>{price_display}")
        
        # Line 2: Position info
        lines.append(f"{side_badge} • {leverage} • {status_text}")
        lines.append("")
        
        # --- STATUS BLOCK: Hybrid Approach ---
        if status == RecommendationStatus.PENDING:
            lines.append("⏳ <b>WAITING FOR ENTRY</b>")
            lines.append(f"Entry Order: {_format_price_clean(entry)}")
            if live_price:
                distance = _pct(entry, live_price, side)
                lines.append(f"Market: {_format_price_clean(live_price)} ({abs(distance):.2f}% away)")
        
        elif status == RecommendationStatus.CLOSED:
            lines.append(f"{icon} <b>TRADE COMPLETED</b>")
            
            # Show REAL PnL (weighted average)
            exit_price_display = _format_price_clean(pnl_data['weighted_exit_price'] or _get_attr(rec, 'exit_price', 0))
            
            # Duration (from v9.0.0)
            duration = ""
            if hasattr(rec, 'created_at') and hasattr(rec, 'closed_at') and rec.closed_at:
                diff = rec.closed_at - rec.created_at
                hours = diff.seconds // 3600
                minutes = (diff.seconds % 3600) // 60
                if hours > 0 or minutes > 0:
                    duration = f" ({hours}h {minutes}m)"
            
            lines.append(f"Final PnL: <b>{_format_pnl_display(pnl_data['total_pnl'])}</b>{duration}")
            lines.append(f"Avg Exit: {exit_price_display}")
            
            # Marketing message (from v11.0.0)
            if pnl_data['total_pnl'] > 0:
                lines.append("<i>🎯 Precise target execution with CapitalGuard</i>")
            elif pnl_data['realized_pnl'] > 0:
                lines.append("<i>💰 Partial profits secured • Risk managed</i>")
            else:
                lines.append("<i>📈 Valuable insights for next trade</i>")
        
        else:  # ACTIVE
            if is_initial_publish:
                lines.append("🚀 <b>TRADE ACTIVATED</b>")
                lines.append("<i>Position opened • Monitoring live</i>")
            elif live_price:
                current_pnl = _pct(entry, live_price, side)
                
                # Calculate total PnL (Realized + Unrealized)
                total_current_pnl = pnl_data['realized_pnl']
                remaining_pct = 100.0 - pnl_data['closed_percentage']
                if remaining_pct > 0:
                    total_current_pnl += (current_pnl * remaining_pct / 100)
                
                lines.append("🚀 <b>LIVE TRADING</b>")
                lines.append(f"Current: {_format_price_clean(live_price)} ({_format_pnl_display(total_current_pnl)})")
                
                # Progress bar for first target (from v9.0.0)
                targets = _get_attr(rec, 'targets', [])
                target_list = targets.values if hasattr(targets, 'values') else []
                
                if target_list and live_price:
                    first_target = _to_decimal(_get_attr(target_list[0], 'price', entry))
                    total_dist = abs(first_target - entry)
                    current_dist = abs(live_price - entry)
                    progress = min(100, (current_dist / total_dist * 100)) if total_dist > 0 else 0
                    
                    bar = _draw_progress_bar(progress)
                    lines.append(f"Progress: {bar}")
                
                # Show realized profits if any
                if pnl_data['realized_pnl'] > 0:
                    lines.append(f"✅ Secured: {_format_pnl_display(pnl_data['realized_pnl'])} ({pnl_data['closed_percentage']:.0f}%)")
            else:
                lines.append("🚀 <b>ACTIVE TRADE</b>")
                lines.append("<i>Real-time tracking enabled</i>")
        
        lines.append(DIVIDER)
        
        # --- TRADING PLAN ---
        lines.append("🎯 <b>TRADING PLAN</b>")
        lines.append(f"Entry: {_format_price_clean(entry)}")
        lines.append(f"Stop: {_format_price_clean(stop_loss)}")
        
        # Risk calculation (from v11.0.0)
        risk_pct = abs((entry - stop_loss) / entry * 100) if entry > 0 else 0
        lines.append(f"Risk: {risk_pct:.1f}%")
        
        lines.append(DIVIDER)
        
        # --- TARGETS with SMART ICONS (from v9.0.0) ---
        targets = _get_attr(rec, 'targets', [])
        target_list = targets.values if hasattr(targets, 'values') else []
        
        if target_list:
            lines.append("🎯 <b>PROFIT TARGETS</b>")
            
            # Track hit targets
            hit_targets = set()
            if rec.events:
                for event in rec.events:
                    event_type = getattr(event, 'event_type', '')
                    if "TP" in event_type and "HIT" in event_type:
                        try:
                            target_num = int(''.join(filter(str.isdigit, event_type)))
                            hit_targets.add(target_num)
                        except:
                            pass
            
            for i, target in enumerate(target_list, 1):
                price = _get_attr(target, 'price', 0)
                profit_pct = _pct(entry, price, side)
                close_pct = target.get('close_percent', 0) if isinstance(target, dict) else getattr(target, 'close_percent', 0)
                
                # Smart icon selection (from v9.0.0)
                icon = _get_target_icon(i, hit_targets, len(target_list))
                
                # Format price
                if i in hit_targets:
                    price_fmt = f"<s>{_format_price_clean(price)}</s>"
                elif icon == "🚀":
                    price_fmt = f"<b>{_format_price_clean(price)}</b>"
                else:
                    price_fmt = _format_price_clean(price)
                
                # Close percentage
                close_tag = f" 📦{int(close_pct)}%" if close_pct > 0 else ""
                
                lines.append(f"{icon} TP{i}: {price_fmt} (+{profit_pct:.1f}%){close_tag}")
            
            # Close summary if partial closes (from v11.0.0)
            total_close_pct = sum(float(getattr(t, 'close_percent', 0)) for t in target_list)
            if total_close_pct > 0:
                lines.append(f"\n📊 <b>Close Plan:</b> {total_close_pct:.0f}% at targets")
        
        # --- TIMELINE (from v9.0.0) ---
        timeline = _build_clean_timeline(rec)
        if timeline:
            lines.append(DIVIDER)
            lines.append(timeline)
        
        # --- ANALYSIS NOTES (from v11.0.0) ---
        notes = getattr(rec, 'notes', '')
        if notes and len(notes.strip()) > 10:
            clean_notes = re.sub(r'Lev:?\s*\d+x?\s*\|?', '', notes, flags=re.IGNORECASE).strip()
            if clean_notes:
                lines.append(DIVIDER)
                short_notes = clean_notes[:60] + "..." if len(clean_notes) > 60 else clean_notes
                lines.append(f"📝 {short_notes}")
        
        # --- CALL TO ACTION ---
        lines.append(DIVIDER)
        lines.append(f"🔍 <a href='{link}'><b>View Detailed Analytics & Live Charts</b></a>")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error(f"Trade card error: {e}")
        return f"📊 <b>TRADING SIGNAL</b>\n\n🔍 <a href='https://t.me/CapitalGuardBot'>View Details</a>"

# --- Portfolio Views: Enhanced from v11.0.0 ---
class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any], 
                        active_count: int, watchlist_count: int, is_analyst: bool):
        """Portfolio hub with marketing focus"""
        try:
            from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace
            
            # Header with marketing message
            header_lines = [
                f"🏆 <b>CapitalGuard Portfolio</b>",
                f"Welcome back, {user_name}",
                "",
                "📈 <b>Your Trading Dashboard</b>",
                "Real-time tracking • Precise analytics • Smart management"
            ]
            
            # Performance metrics
            win_rate = report.get('win_rate_pct', 'N/A')
            total_pnl = report.get('total_pnl_pct', '0%')
            
            stats_lines = [
                "────────────────",
                "🎯 <b>PERFORMANCE SUMMARY</b>",
                f"• Win Rate: <b>{win_rate}</b>",
                f"• Total PnL: <b>{total_pnl}</b>",
                f"• Active Trades: <b>{active_count}</b>",
                f"• Watchlist: <b>{watchlist_count}</b>",
                "────────────────",
                "<b>QUICK ACCESS</b>"
            ]
            
            # Navigation buttons
            ns = CallbackNamespace.MGMT
            keyboard = [
                [InlineKeyboardButton(f"🚀 Live Trades ({active_count})", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
                [InlineKeyboardButton(f"👁️ Watchlist ({watchlist_count})", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
                [InlineKeyboardButton("📜 Trade History", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))]
            ]
            
            if is_analyst:
                keyboard.append([InlineKeyboardButton("📈 Analyst Dashboard", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])
            
            keyboard.append([InlineKeyboardButton("🔄 Refresh Dashboard", 
                callback_data=CallbackBuilder.create(ns, "hub"))])
            
            # Combine text
            text = "\n".join(header_lines + stats_lines)
            
            # Send or update message
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
            else:
                await update.effective_message.reply_text(
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                
        except BadRequest:
            pass
        except Exception as e:
            log.warning(f"Portfolio hub error: {e}")

# --- Review Screen (Clean) ---
def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float] = None) -> str:
    """Clean review screen for analysts"""
    asset = draft.get("asset", "SYMBOL").upper()
    side = draft.get("side", "LONG")
    entry = _to_decimal(draft.get("entry", 0))
    sl = _to_decimal(draft.get("stop_loss", 0))
    
    icon = "🟢" if side == "LONG" else "🔴"
    
    lines = [
        f"🛡️ <b>CONFIRM TRADING SIGNAL</b>",
        "",
        f"💎 <b>#{asset}</b>",
        f"Direction: {icon} <b>{side}</b>",
        f"Entry: {_format_price_clean(entry)}",
        f"Stop: {_format_price_clean(sl)}"
    ]
    
    # Targets preview
    targets = draft.get("targets", [])
    if targets:
        lines.append("")
        lines.append("🎯 <b>TARGETS</b>")
        for i, target in enumerate(targets, 1):
            price = _to_decimal(target.get('price', 0))
            close_pct = target.get('close_percent', 0)
            close_tag = f" 📦{int(close_pct)}%" if close_pct > 0 else ""
            lines.append(f"TP{i}: {_format_price_clean(price)}{close_tag}")
    
    lines.append("")
    lines.append("📤 <i>Ready to publish to channels?</i>")
    
    return "\n".join(lines)

# --- END OF HYBRID VERSION ---