# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v11.0.0-ULTIMATE (Visual Identity + Real PnL + Marketing)
# ✅ PHILOSOPHY:
#     1. Strong Visual Identity: Unique icons for each state (🏆 💎 🚀 ⏳)
#     2. Real PnL Calculation: Weighted average for partial closes
#     3. Live Price Integration: Real-time prices from cache
#     4. Marketing Focus: Highlights system benefits
#     5. Clean & Focused: No clutter, essential info only

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

# --- CORE: Live Price Integration ---
async def get_live_price(symbol: str, market: str = "Futures") -> Optional[float]:
    """Get real-time price from Redis cache"""
    try:
        # Lazy import to avoid circular dependency with boot/core
        from capitalguard.infrastructure.core_engine import core_cache
        
        # Try Futures first
        cache_key = f"price:{market.upper()}:{symbol}"
        price = await core_cache.get(cache_key)
        
        if price:
            return float(price)
        
        # Fallback to other market
        alt_market = "SPOT" if market == "Futures" else "Futures"
        alt_key = f"price:{alt_market}:{symbol}"
        price = await core_cache.get(alt_key)
        
        return float(price) if price else None
        
    except Exception as e:
        # Log debug to not spam errors if cache is warming up
        log.debug(f"Live price fetch failed for {symbol}: {e}")
        return None

# --- CORE: Real PnL Calculator (Weighted Average) ---
def calculate_real_pnl(rec: Recommendation) -> Dict[str, Any]:
    """
    Calculates TRUE PnL considering ALL partial closes.
    Returns weighted average exit price and accurate profit/loss.
    """
    try:
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        side = _get_attr(rec.side, 'value', 'LONG')
        status = _get_attr(rec, 'status')
        
        # Track partial closes from events
        partial_closes = []
        total_closed_pct = 0.0
        
        if rec.events:
            for event in rec.events:
                event_type = getattr(event, 'event_type', '')
                event_data = getattr(event, 'event_data', {}) or {}
                
                # Look for partial close events
                if "PARTIAL" in str(event_type):
                    close_price = event_data.get('price')
                    close_pct = event_data.get('amount', 0) # or 'closed_percent'
                    if not close_pct: close_pct = event_data.get('closed_percent', 0)
                    
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
            
            # If there was remaining position, add its PnL
            if remaining_pct > 0.1: # Tolerance for float math
                remaining_pnl = _pct(entry, exit_price, side)
                final_pnl += (remaining_pnl * remaining_pct / 100)
            
            # Calculate weighted average exit price
            if partial_closes:
                weighted_price_sum = sum(c['price'] * c['percentage'] for c in partial_closes)
                if remaining_pct > 0.1:
                    weighted_price_sum += float(exit_price) * remaining_pct
                weighted_exit_price = weighted_price_sum / 100
            else:
                weighted_exit_price = float(exit_price)
        else:
            # If active, total PnL is just realized so far
            pass
        
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
            'total_pnl': 0.0, 'realized_pnl': 0.0, 'closed_percentage': 0.0,
            'weighted_exit_price': None, 'is_closed': False, 'partial_closes_count': 0
        }

# --- VISUAL IDENTITY: Icons & Formatting ---
def _get_visual_identity(status: RecommendationStatus, pnl: float = 0) -> tuple:
    """Returns (icon, color_tag, status_text) for strong visual identity."""
    if status == RecommendationStatus.PENDING:
        return ("⏳", "", "PENDING ORDER")
    
    elif status == RecommendationStatus.CLOSED:
        if pnl > 0:
            return ("🏆", "🟢", "WIN - TARGETS HIT")
        else:
            return ("💎", "🔴", "CLOSED - STOP LOSS")
    
    else:  # ACTIVE
        return ("🚀", "🔵", "ACTIVE TRADE")

def _format_pnl_display(pnl: float) -> str:
    """Clean PnL formatting with marketing focus."""
    if pnl > 15: return f"🎯 +{pnl:.2f}%"
    if pnl > 8: return f"💰 +{pnl:.2f}%"
    if pnl > 0: return f"💚 +{pnl:.2f}%"
    if pnl < -15: return f"💀 {pnl:.2f}%"
    if pnl < -8: return f"🔻 {pnl:.2f}%"
    if pnl < 0: return f"🔸 {pnl:.2f}%"
    return "⚪ 0.00%"

def _format_price_clean(price) -> str:
    """Clean price formatting."""
    try:
        num = float(price)
        if num >= 1000: return f"{num:,.2f}"
        elif num >= 1: return f"{num:.3f}"
        else: return f"{num:.5f}"
    except: return str(price)

def _extract_leverage(notes: str) -> str:
    if not notes: return "20x"
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

# --- CARD BUILDERS: Clean & Focused ---
async def build_trade_card_text(rec: Recommendation, bot_username: str, is_initial_publish: bool = False) -> str:
    """
    Ultimate trade card with visual identity and marketing focus.
    NOTE: This is an ASYNC function because it fetches live prices.
    """
    try:
        # Basic data
        symbol = _get_attr(rec.asset, 'value', 'SYMBOL')
        side = _get_attr(rec.side, 'value', 'LONG')
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        stop_loss = _to_decimal(_get_attr(rec, 'stop_loss', 0))
        status = _get_attr(rec, 'status')
        
        # Live price (Async)
        market = getattr(rec, 'market', 'Futures') or 'Futures'
        live_price = await get_live_price(symbol, market)
        
        # Real PnL calculation
        pnl_data = calculate_real_pnl(rec)
        
        # Visual identity
        icon, _, status_text = _get_visual_identity(status, pnl_data['total_pnl'])
        
        # Header
        side_badge = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        leverage = _extract_leverage(getattr(rec, 'notes', ''))
        
        # Live price in header if active
        price_display = ""
        if live_price and status == RecommendationStatus.ACTIVE:
            price_display = f" • {_format_price_clean(live_price)}"
        
        safe_username = bot_username.replace("@", "")
        link = f"https://t.me/{safe_username}/{WEBAPP_SHORT_NAME}?startapp={rec.id}"
        
        # Start building lines
        lines = []
        DIVIDER = "────────────────"
        
        # Line 1: Visual Identity
        lines.append(f"{icon} <b>#{symbol}</b>{price_display}")
        
        # Line 2: Position info
        lines.append(f"{side_badge} • {leverage} • {status_text}")
        lines.append("")
        
        # --- STATUS BLOCK ---
        if status == RecommendationStatus.PENDING:
            lines.append(f"Entry Order: <b>{_format_price_clean(entry)}</b>")
            if live_price:
                distance = _pct(entry, live_price, side)
                lines.append(f"Market: {_format_price_clean(live_price)} ({abs(distance):.2f}% away)")
        
        elif status == RecommendationStatus.CLOSED:
            # Show REAL PnL (weighted average)
            exit_price_display = _format_price_clean(pnl_data['weighted_exit_price'] or _get_attr(rec, 'exit_price', 0))
            
            # Duration
            duration = ""
            if hasattr(rec, 'created_at') and hasattr(rec, 'closed_at') and rec.closed_at:
                diff = rec.closed_at - rec.created_at
                hours = diff.seconds // 3600
                if diff.days > 0: duration = f" ({diff.days}d)"
                elif hours > 0: duration = f" ({hours}h)"
            
            lines.append(f"🏁 Final PnL: <b>{_format_pnl_display(pnl_data['total_pnl'])}</b>{duration}")
            lines.append(f"📉 Avg Exit: {exit_price_display}")
            
            # Context message
            if pnl_data['total_pnl'] > 0:
                lines.append("<i>🎯 Targets hit successfully</i>")
            else:
                lines.append("<i>🛑 Stop loss executed</i>")
        
        else:  # ACTIVE
            if is_initial_publish:
                lines.append("<i>Position opened • Monitoring live</i>")
            elif live_price:
                current_pnl = _pct(entry, live_price, side)
                # Calculate total PnL (Realized + Unrealized)
                total_current_pnl = pnl_data['realized_pnl']
                
                # Add unrealized part
                remaining_pct = 100.0 - pnl_data['closed_percentage']
                if remaining_pct > 0:
                    total_current_pnl += (current_pnl * remaining_pct / 100)
                
                lines.append(f"PnL: <b>{_format_pnl_display(total_current_pnl)}</b>")
                lines.append(f"Entry: {_format_price_clean(entry)}")
                
                if pnl_data['realized_pnl'] > 0:
                    lines.append(f"<i>💰 Locked: {_format_pnl_display(pnl_data['realized_pnl'])}</i>")

        lines.append(DIVIDER)
        
        # --- TRADING PLAN ---
        # Show Stop Loss and Targets
        lines.append(f"🛑 Stop: <b>{_format_price_clean(stop_loss)}</b>")
        
        targets = _get_attr(rec, 'targets', [])
        target_list = targets.values if hasattr(targets, 'values') else []
        
        if target_list:
            # Track hit targets from events
            hit_targets = set()
            if rec.events:
                for event in rec.events:
                    if "TP" in str(getattr(event, 'event_type', '')) and "HIT" in str(getattr(event, 'event_type', '')):
                        try:
                            target_num = int(''.join(filter(str.isdigit, event.event_type)))
                            hit_targets.add(target_num)
                        except: pass
            
            # Display targets
            for i, target in enumerate(target_list, 1):
                price = _get_attr(target, 'price', 0)
                profit_pct = _pct(entry, price, side)
                close_pct = target.get('close_percent', 0) if isinstance(target, dict) else getattr(target, 'close_percent', 0)
                
                # Icon Logic
                if i in hit_targets:
                    icon = "✅"
                    price_fmt = f"<s>{_format_price_clean(price)}</s>"
                elif not hit_targets and i == 1:
                    icon = "🚀" # Next target
                    price_fmt = f"<b>{_format_price_clean(price)}</b>"
                elif i > max(hit_targets) if hit_targets else i == 1:
                    icon = "🚀" if (max(hit_targets) + 1) == i else "⏳"
                    price_fmt = f"<b>{_format_price_clean(price)}</b>" if icon == "🚀" else _format_price_clean(price)
                else:
                    icon = "⏳"
                    price_fmt = _format_price_clean(price)
                
                close_tag = f" 📦{int(close_pct)}%" if close_pct > 0 else ""
                lines.append(f"{icon} TP{i}: {price_fmt} ({profit_pct:.1f}%){close_tag}")

        # --- CALL TO ACTION ---
        lines.append(DIVIDER)
        lines.append(f"🔍 <a href='{link}'><b>View Analytics & Control</b></a>")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error(f"Trade card error: {e}", exc_info=True)
        return f"📊 <b>TRADING SIGNAL</b>\n\nError building card.\n🔍 <a href='https://t.me/{bot_username}'>View Details</a>"

# --- Portfolio Views ---
class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any], 
                        active_count: int, watchlist_count: int, is_analyst: bool):
        try:
            from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace
            
            header = f"🏆 <b>CapitalGuard Portfolio</b>\nTrader: {user_name}"
            win_rate = report.get('win_rate_pct', 'N/A')
            total_pnl = report.get('total_pnl_pct', '0%')
            
            stats = (
                "────────────────\n"
                "📊 <b>PERFORMANCE</b>\n"
                f"• Win Rate: <b>{win_rate}</b>\n"
                f"• Total PnL: <b>{total_pnl}</b>\n"
                f"• Active: <b>{active_count}</b> | Pending: <b>{watchlist_count}</b>\n"
                "────────────────"
            )
            
            ns = CallbackNamespace.MGMT
            keyboard = [
                [InlineKeyboardButton(f"🚀 Active Trades ({active_count})", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
                [InlineKeyboardButton(f"👁️ Watchlist ({watchlist_count})", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
                [InlineKeyboardButton("📜 History", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))]
            ]
            
            if is_analyst:
                keyboard.append([InlineKeyboardButton("📈 Analyst Tools", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])
            
            keyboard.append([InlineKeyboardButton("🔄 Refresh", 
                callback_data=CallbackBuilder.create(ns, "hub"))])
            
            text = f"{header}\n\n{stats}"
            
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
        except BadRequest: pass
        except Exception as e: log.warning(f"Portfolio hub error: {e}")

# --- Review Text ---
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
        f"Entry: <code>{_format_price_clean(entry)}</code>\n"
        f"Stop: <code>{_format_price_clean(sl)}</code>\n"
    )
    
    targets = draft.get("targets", [])
    if targets:
        text += f"\n🎯 <b>TARGETS:</b>\n"
        for i, target in enumerate(targets, 1):
            price = _to_decimal(target.get('price', 0))
            pct = target.get('close_percent', 0)
            tag = f" 📦{int(pct)}%" if pct > 0 else ""
            text += f"TP{i}: {_format_price_clean(price)}{tag}\n"
    
    text += f"\n📤 <i>Publish now?</i>"
    return text

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---