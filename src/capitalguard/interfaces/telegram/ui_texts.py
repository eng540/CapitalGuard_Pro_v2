# --- START OF PROFESSIONAL FIXED VERSION: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v11.2.1-HYBRID-FIXED (Professional Data Handling)
# ✅ PROFESSIONAL DATA HANDLING:
#     1. Strict Decimal operations for financial calculations
#     2. Safe type conversions with validation
#     3. Consistent data types throughout
#     4. Comprehensive error handling

from __future__ import annotations
import logging
import re
from typing import List, Optional, Dict, Any, Union
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.error import BadRequest

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.interfaces.telegram.helpers import _get_attr, _to_decimal, _pct, _format_price

log = logging.getLogger(__name__)

# ============================================================================
# PROFESSIONAL DATA TYPE HANDLING
# ============================================================================

def _to_decimal_safe(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """Safely convert any value to Decimal with professional error handling"""
    if isinstance(value, Decimal):
        return value
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            # Remove any currency symbols and clean the string
            clean_value = re.sub(r'[^\d.-]', '', value.strip())
            return Decimal(clean_value) if clean_value else default
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as e:
        log.warning(f"Decimal conversion failed: {value} -> {e}")
        return default

def _to_float_safe(value: Any, default: float = 0.0) -> float:
    """Safely convert any value to float with fallback"""
    try:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, Decimal):
            return float(value)
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError) as e:
        log.warning(f"Float conversion failed: {value} -> {e}")
        return default

def _calculate_safe(operation: str, *args) -> Decimal:
    """Perform safe mathematical operations with Decimals"""
    try:
        decimals = [_to_decimal_safe(arg) for arg in args]
        
        if operation == 'subtract' and len(decimals) == 2:
            return decimals[0] - decimals[1]
        elif operation == 'divide' and len(decimals) == 2:
            if decimals[1] == Decimal('0'):
                return Decimal('0')
            return decimals[0] / decimals[1]
        elif operation == 'abs_diff' and len(decimals) == 2:
            return abs(decimals[0] - decimals[1])
        elif operation == 'multiply' and len(decimals) == 2:
            return decimals[0] * decimals[1]
        else:
            return Decimal('0')
    except Exception as e:
        log.error(f"Safe calculation failed: {operation} -> {e}")
        return Decimal('0')

# ============================================================================
# CORE FUNCTIONS WITH PROFESSIONAL DATA HANDLING
# ============================================================================

# --- Configuration ---
WEBAPP_SHORT_NAME = "terminal"
APP_NAME = "CapitalGuard"

# --- CORE: Live Price Integration ---
async def get_live_price(symbol: str, market: str = "Futures") -> Optional[Decimal]:
    """Get real-time price as Decimal for precision"""
    try:
        from capitalguard.infrastructure.core_engine import core_cache
        
        cache_key = f"price:{market.upper()}:{symbol}"
        price = await core_cache.get(cache_key)
        
        if price:
            return _to_decimal_safe(price)
        
        alt_market = "SPOT" if market == "Futures" else "Futures"
        alt_key = f"price:{alt_market}:{symbol}"
        price = await core_cache.get(alt_key)
        
        return _to_decimal_safe(price) if price else None
        
    except Exception as e:
        log.debug(f"Live price fetch failed for {symbol}: {e}")
        return None

# --- CORE: Professional PnL Calculator ---
def calculate_real_pnl(rec: Recommendation) -> Dict[str, Any]:
    """
    Professional PnL calculation with strict Decimal operations
    """
    try:
        entry = _to_decimal_safe(_get_attr(rec, 'entry', 0))
        side = _get_attr(rec.side, 'value', 'LONG')
        status = _get_attr(rec, 'status')
        
        # Track partial closes
        partial_closes = []
        total_closed_pct = Decimal('0')
        total_realized_pnl = Decimal('0')
        
        if rec.events:
            for event in rec.events:
                event_type = getattr(event, 'event_type', '')
                event_data = getattr(event, 'event_data', {}) or {}
                
                if "PARTIAL_CLOSE" in event_type:
                    close_price = event_data.get('price')
                    close_pct = event_data.get('closed_percent', 0)
                    
                    if close_price and close_pct > 0:
                        close_price_dec = _to_decimal_safe(close_price)
                        close_pct_dec = _to_decimal_safe(close_pct)
                        
                        # Calculate profit at close using Decimal
                        if side == "LONG":
                            profit_pct = ((close_price_dec - entry) / entry) * Decimal('100')
                        else:  # SHORT
                            profit_pct = ((entry - close_price_dec) / entry) * Decimal('100')
                        
                        partial_closes.append({
                            'price': close_price_dec,
                            'percentage': close_pct_dec,
                            'profit': profit_pct,
                            'profit_float': float(profit_pct)
                        })
                        
                        total_closed_pct += close_pct_dec
                        total_realized_pnl += (profit_pct * close_pct_dec) / Decimal('100')
        
        # Final calculations with Decimal precision
        is_closed = (status == RecommendationStatus.CLOSED)
        weighted_exit_price = None
        total_pnl = total_realized_pnl
        
        if is_closed:
            exit_price = _to_decimal_safe(_get_attr(rec, 'exit_price', 0))
            remaining_pct = Decimal('100') - total_closed_pct
            
            if remaining_pct > Decimal('0.1'):
                # Calculate remaining PnL
                if side == "LONG":
                    remaining_pnl = ((exit_price - entry) / entry) * Decimal('100')
                else:  # SHORT
                    remaining_pnl = ((entry - exit_price) / entry) * Decimal('100')
                
                total_pnl += (remaining_pnl * remaining_pct) / Decimal('100')
            
            # Calculate weighted average exit price
            if partial_closes:
                weighted_sum = sum(
                    (c['price'] * c['percentage']) 
                    for c in partial_closes
                )
                if remaining_pct > Decimal('0.1'):
                    weighted_sum += exit_price * remaining_pct
                weighted_exit_price = weighted_sum / Decimal('100')
            else:
                weighted_exit_price = exit_price
        
        # Convert to appropriate types for return
        return {
            'total_pnl': float(total_pnl.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            'realized_pnl': float(total_realized_pnl.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            'closed_percentage': float(total_closed_pct.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            'weighted_exit_price': float(weighted_exit_price) if weighted_exit_price else None,
            'is_closed': is_closed,
            'partial_closes_count': len(partial_closes),
            'partial_closes': partial_closes
        }
        
    except Exception as e:
        log.error(f"Professional PnL calculation error: {e}", exc_info=True)
        return {
            'total_pnl': 0.0,
            'realized_pnl': 0.0,
            'closed_percentage': 0.0,
            'weighted_exit_price': None,
            'is_closed': False,
            'partial_closes_count': 0,
            'partial_closes': []
        }

# ============================================================================
# VISUAL & FORMATTING FUNCTIONS
# ============================================================================

def _get_visual_identity(status: RecommendationStatus, pnl: float = 0) -> tuple:
    """Professional visual identity system"""
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
    """Professional PnL formatting"""
    if pnl > 15:
        return f"🎯 +{pnl:.2f}%"
    elif pnl > 8:
        return f"💰 +{pnl:.2f}%"
    elif pnl > 0:
        return f"💚 +{pnl:.2f}%"
    elif pnl < -15:
        return f"📉 {pnl:.2f}%"
    elif pnl < -8:
        return f"⚫ {pnl:.2f}%"
    elif pnl < 0:
        return f"🔸 {pnl:.2f}%"
    else:
        return "⚪ 0.00%"

def _format_price_clean(price: Any) -> str:
    """Professional price formatting"""
    try:
        price_dec = _to_decimal_safe(price)
        price_float = float(price_dec)
        
        if price_float >= 1000:
            return f"${price_float:,.0f}"
        elif price_float >= 1:
            return f"${price_float:.2f}"
        else:
            # For crypto prices < 1
            if price_float >= 0.01:
                return f"${price_float:.4f}"
            elif price_float >= 0.0001:
                return f"${price_float:.6f}"
            else:
                return f"${price_float:.8f}"
    except Exception:
        return str(price)

def _extract_leverage(notes: str, market: str = "Futures") -> str:
    """Professional leverage extraction"""
    if "SPOT" in market.upper():
        return "1x (Spot)"
    
    if not notes:
        return "20x"
    
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

def _draw_progress_bar(percent: float, length: int = 8) -> str:
    """Create visual progress bar"""
    percent = max(0, min(100, percent))
    filled = int(length * percent // 100)
    return "█" * filled + "░" * (length - filled)

def _build_clean_timeline(rec: Recommendation) -> str:
    """Professional timeline builder"""
    if not rec.events:
        return ""
    
    IGNORED_EVENTS = ["CREATED", "CREATED_ACTIVE", "CREATED_PENDING", "PUBLISHED"]
    meaningful_events = [
        e for e in rec.events 
        if getattr(e, 'event_type', '') not in IGNORED_EVENTS
    ]
    
    if not meaningful_events:
        return ""
    
    events_sorted = sorted(
        meaningful_events,
        key=lambda e: getattr(e, 'event_timestamp', datetime.now()),
        reverse=True
    )[:3]
    
    lines = ["🕐 <b>Recent Activity:</b>"]
    
    for event in events_sorted:
        ts = getattr(event, 'event_timestamp', datetime.now()).strftime("%H:%M")
        e_type = getattr(event, 'event_type', '').replace("_", " ").title()
        
        # Professional event type formatting
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
    """Professional target icon selection"""
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

def _calculate_risk_percentage(entry: Decimal, stop_loss: Decimal) -> float:
    """Professional risk calculation"""
    try:
        if entry == Decimal('0'):
            return 0.0
        
        risk_pct = (abs(entry - stop_loss) / entry) * Decimal('100')
        return float(risk_pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0

# ============================================================================
# MAIN CARD BUILDER - PROFESSIONAL VERSION
# ============================================================================

async def build_trade_card_text(rec: Recommendation, bot_username: str, is_initial_publish: bool = False) -> str:
    """
    Professional trade card with strict data type handling
    """
    try:
        # ===== PROFESSIONAL DATA EXTRACTION =====
        symbol = _get_attr(rec.asset, 'value', 'SYMBOL').upper()
        side = _get_attr(rec.side, 'value', 'LONG')
        entry = _to_decimal_safe(_get_attr(rec, 'entry', 0))
        stop_loss = _to_decimal_safe(_get_attr(rec, 'stop_loss', 0))
        status = _get_attr(rec, 'status')
        market = getattr(rec, 'market', 'Futures') or 'Futures'
        
        # ===== LIVE DATA WITH PROFESSIONAL HANDLING =====
        live_price_dec = await get_live_price(symbol, market)
        
        # Determine display price with fallback
        if live_price_dec is not None:
            display_price = live_price_dec
            display_price_float = float(live_price_dec)
        else:
            display_price = entry
            display_price_float = float(entry)
        
        # ===== PROFESSIONAL CALCULATIONS =====
        pnl_data = calculate_real_pnl(rec)
        status_identity = _get_visual_identity(status, pnl_data['total_pnl'])
        
        # Risk calculation with Decimal precision
        risk_pct = _calculate_risk_percentage(entry, stop_loss)
        
        # Current PnL if active (using Decimal for calculation)
        current_pnl_float = 0.0
        if live_price_dec is not None and status == RecommendationStatus.ACTIVE:
            if side == "LONG":
                current_pnl_dec = ((live_price_dec - entry) / entry) * Decimal('100')
            else:  # SHORT
                current_pnl_dec = ((entry - live_price_dec) / entry) * Decimal('100')
            current_pnl_float = float(current_pnl_dec)
        
        # ===== HEADER CONSTRUCTION =====
        leverage = _extract_leverage(getattr(rec, 'notes', ''), market)
        side_badge = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        
        lines = []
        DIVIDER = "────────────────"
        
        # Line 1: Visual Identity + Symbol + Price (ALWAYS VISIBLE)
        price_display = f" • {_format_price_clean(display_price)}"
        lines.append(f"{status_identity[0]} <b>#{symbol}</b>{price_display}")
        
        # Line 2: Position info
        status_label = status_identity[2]
        if status == RecommendationStatus.ACTIVE and pnl_data['realized_pnl'] > 0:
            status_label = f"LIVE • {pnl_data['closed_percentage']:.0f}% Secured"
        
        lines.append(f"{side_badge} • {leverage} • {status_label}")
        lines.append("")
        
        # ===== STATUS DASHBOARD WITH DECIMAL PRECISION =====
        if status == RecommendationStatus.PENDING:
            lines.append("⏳ <b>WAITING FOR ENTRY</b>")
            lines.append(f"Order: {_format_price_clean(entry)}")
            
            if live_price_dec is not None:
                # Calculate distance using Decimal
                distance_dec = _calculate_safe('abs_diff', live_price_dec, entry)
                if entry != Decimal('0'):
                    distance_pct = (distance_dec / entry) * Decimal('100')
                    lines.append(f"Market: {_format_price_clean(live_price_dec)} ({float(distance_pct):.2f}% away)")
        
        elif status == RecommendationStatus.CLOSED:
            lines.append(f"{status_identity[0]} <b>TRADE COMPLETED</b>")
            
            # Show weighted exit price
            exit_price = pnl_data['weighted_exit_price'] or _get_attr(rec, 'exit_price', 0)
            lines.append(f"Avg Exit: {_format_price_clean(exit_price)}")
            
            # Final PnL
            lines.append(f"Final PnL: <b>{_format_pnl_display(pnl_data['total_pnl'])}</b>")
            
            # Professional context message
            if pnl_data['total_pnl'] > 0:
                lines.append(f"<i>🎯 Precision execution with {APP_NAME}</i>")
            elif pnl_data['realized_pnl'] > 0:
                lines.append("<i>💰 Partial profits secured • Risk managed</i>")
            else:
                lines.append("<i>📈 Valuable insights captured for next trade</i>")
        
        else:  # ACTIVE
            lines.append("🚀 <b>LIVE TRADING</b>")
            
            # Calculate total current PnL (realized + unrealized)
            total_current_pnl = pnl_data['realized_pnl']
            if pnl_data['closed_percentage'] < 100 and live_price_dec is not None:
                unrealized_pct = 100 - pnl_data['closed_percentage']
                total_current_pnl += current_pnl_float * (unrealized_pct / 100)
            
            lines.append(f"Position: {_format_price_clean(display_price)} ({_format_pnl_display(total_current_pnl)})")
            
            # Progress bar for first target (professional calculation)
            targets = _get_attr(rec, 'targets', [])
            target_list = targets.values if hasattr(targets, 'values') else []
            
            if target_list and live_price_dec is not None:
                first_target = _to_decimal_safe(_get_attr(target_list[0], 'price', entry))
                
                # Calculate progress with Decimal precision
                total_distance = _calculate_safe('abs_diff', first_target, entry)
                current_distance = _calculate_safe('abs_diff', live_price_dec, entry)
                
                if total_distance != Decimal('0'):
                    progress_dec = (current_distance / total_distance) * Decimal('100')
                    progress = min(100, float(progress_dec))
                    
                    bar = _draw_progress_bar(progress)
                    lines.append(f"Progress: {bar}")
            
            # Show realized profits if any
            if pnl_data['realized_pnl'] > 0:
                lines.append(f"✅ Secured: {_format_pnl_display(pnl_data['realized_pnl'])} ({pnl_data['closed_percentage']:.0f}%)")
        
        lines.append(DIVIDER)
        
        # ===== PROFESSIONAL TRADING PLAN =====
        lines.append("🎯 <b>TRADING PLAN</b>")
        lines.append(f"{'📈' if side == 'LONG' else '📉'} Entry: {_format_price_clean(entry)}")
        lines.append(f"🛑 Stop: {_format_price_clean(stop_loss)}")
        lines.append(f"⚖️ Risk: {risk_pct:.1f}%")
        
        # ===== TARGETS WITH PROFESSIONAL ICONS =====
        if target_list:
            lines.append(DIVIDER)
            lines.append("🎯 <b>PROFIT TARGETS</b>")
            
            # Determine hit targets
            hit_targets = set()
            if rec.events:
                for event in rec.events:
                    event_type = getattr(event, 'event_type', '')
                    if "TP" in event_type and "HIT" in event_type:
                        try:
                            target_num = int(''.join(filter(str.isdigit, event_type)))
                            hit_targets.add(target_num)
                        except (ValueError, TypeError):
                            pass
            
            for i, target in enumerate(target_list, 1):
                price = _to_decimal_safe(_get_attr(target, 'price', 0))
                
                # Calculate profit percentage with Decimal
                if side == "LONG":
                    profit_pct = ((price - entry) / entry) * Decimal('100')
                else:  # SHORT
                    profit_pct = ((entry - price) / entry) * Decimal('100')
                
                profit_pct_float = float(profit_pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))
                
                # Professional icon selection
                icon = _get_target_icon(i, hit_targets, len(target_list))
                
                # Close percentage
                close_pct = target.get('close_percent', 0) if isinstance(target, dict) else getattr(target, 'close_percent', 0)
                close_tag = f" 📦{int(close_pct)}%" if close_pct > 0 else ""
                
                # Format based on status
                if i in hit_targets:
                    price_fmt = f"<s>{_format_price_clean(price)}</s>"
                elif icon == "🚀":
                    price_fmt = f"<b>{_format_price_clean(price)}</b>"
                else:
                    price_fmt = _format_price_clean(price)
                
                lines.append(f"{icon} TP{i}: {price_fmt} (+{profit_pct_float:.1f}%){close_tag}")
            
            # Professional close plan summary
            total_close_pct = sum(
                float(_to_decimal_safe(
                    t.get('close_percent', 0) if isinstance(t, dict) else getattr(t, 'close_percent', 0)
                ))
                for t in target_list
            )
            if total_close_pct > 0:
                lines.append(f"\n📊 <b>Close Plan:</b> {total_close_pct:.0f}% at targets")
        
        # ===== PROFESSIONAL TIMELINE =====
        timeline = _build_clean_timeline(rec)
        if timeline:
            lines.append(DIVIDER)
            lines.append(timeline)
        
        # ===== PROFESSIONAL ANALYSIS NOTES =====
        notes = getattr(rec, 'notes', '')
        if notes and len(notes.strip()) > 10:
            clean_notes = re.sub(r'Lev:?\s*\d+x?\s*\|?', '', notes, flags=re.IGNORECASE).strip()
            if clean_notes:
                lines.append(DIVIDER)
                short_notes = clean_notes[:80] + "..." if len(clean_notes) > 80 else clean_notes
                lines.append(f"📝 <b>Analysis:</b> {short_notes}")
        
        # ===== PROFESSIONAL CALL TO ACTION =====
        safe_username = bot_username.replace("@", "")
        link = f"https://t.me/{safe_username}/{WEBAPP_SHORT_NAME}?startapp={rec.id}"
        
        lines.append(DIVIDER)
        lines.append(f"🔍 <a href='{link}'><b>View Detailed Analytics & Live Charts</b></a>")
        lines.append(f"<i>Powered by {APP_NAME} • Professional trading tools</i>")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error(f"Professional trade card error: {e}", exc_info=True)
        # Professional error message
        return (
            f"📊 <b>{APP_NAME} Trading Signal</b>\n\n"
            f"⚠️ <i>System is updating real-time data...</i>\n"
            f"🔍 <a href='https://t.me/{bot_username}'>View details in app</a>"
        )

# ============================================================================
# PORTFOLIO VIEWS - PROFESSIONAL VERSION
# ============================================================================

class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any], 
                        active_count: int, watchlist_count: int, is_analyst: bool):
        """Professional portfolio dashboard"""
        try:
            from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace
            
            # Professional header
            current_hour = datetime.now().hour
            if current_hour < 12:
                greeting = "🌅 Good morning"
            elif current_hour < 18:
                greeting = "🌆 Good afternoon"
            else:
                greeting = "🌙 Good evening"
            
            header_lines = [
                f"🏆 <b>{APP_NAME} Professional Portfolio</b>",
                f"{greeting}, {user_name}",
                "",
                "📈 <b>Advanced Trading Dashboard</b>",
                "Precision analytics • Risk management • Performance insights"
            ]
            
            # Professional metrics
            win_rate = report.get('win_rate_pct', 'N/A')
            total_pnl = report.get('total_pnl_pct', '0%')
            best_trade = report.get('best_trade_pct', 'N/A')
            avg_duration = report.get('avg_hold_time', 'N/A')
            
            stats_lines = [
                "─" * 32,
                "🎯 <b>PERFORMANCE METRICS</b>",
                f"• Win Rate: <b>{win_rate}</b>",
                f"• Total PnL: <b>{total_pnl}</b>",
                f"• Best Trade: <b>{best_trade}</b>",
                f"• Avg Duration: <b>{avg_duration}</b>",
                "",
                f"• Active Positions: <b>{active_count}</b>",
                f"• Watchlist: <b>{watchlist_count}</b>",
                "─" * 32,
                "<b>QUICK ACCESS</b>"
            ]
            
            # Professional navigation
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
                keyboard.append([InlineKeyboardButton("📈 Analyst Console", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])
            
            keyboard.append([InlineKeyboardButton("🔄 Refresh Dashboard", 
                callback_data=CallbackBuilder.create(ns, "hub"))])
            
            # Professional rendering
            text = "\n".join(header_lines + stats_lines)
            
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
                
        except BadRequest as e:
            log.debug(f"Portfolio update blocked: {e}")
        except Exception as e:
            log.error(f"Professional portfolio error: {e}", exc_info=True)

# ============================================================================
# REVIEW SCREEN - PROFESSIONAL VERSION
# ============================================================================

def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float] = None) -> str:
    """Professional review screen"""
    asset = draft.get("asset", "SYMBOL").upper()
    side = draft.get("side", "LONG")
    entry = _to_decimal_safe(draft.get("entry", 0))
    sl = _to_decimal_safe(draft.get("stop_loss", 0))
    
    icon = "🟢" if side == "LONG" else "🔴"
    direction = "LONG" if side == "LONG" else "SHORT"
    
    lines = [
        f"🛡️ <b>CONFIRM TRADING SIGNAL</b>",
        "",
        f"💎 <b>#{asset}</b>",
        f"Direction: {icon} <b>{direction}</b>",
        f"Entry Price: <code>{_format_price_clean(entry)}</code>",
        f"Stop Loss: <code>{_format_price_clean(sl)}</code>",
    ]
    
    # Professional risk calculation
    risk_pct = _calculate_risk_percentage(entry, sl)
    lines.append(f"Risk: <b>{risk_pct:.1f}%</b>")
    
    # Professional targets preview
    targets = draft.get("targets", [])
    if targets:
        lines.append("")
        lines.append("🎯 <b>PROFIT TARGETS</b>")
        
        for i, target in enumerate(targets, 1):
            price = _to_decimal_safe(target.get('price', 0))
            close_pct = target.get('close_percent', 0)
            
            # Calculate profit percentage
            if side == "LONG":
                profit_pct = ((price - entry) / entry) * Decimal('100')
            else:
                profit_pct = ((entry - price) / entry) * Decimal('100')
            
            profit_pct_float = float(profit_pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))
            close_tag = f" 📦{int(close_pct)}%" if close_pct > 0 else ""
            
            lines.append(f"TP{i}: {_format_price_clean(price)} (+{profit_pct_float:.1f}%){close_tag}")
    
    # Professional market context
    if preview_price is not None:
        preview_price_dec = _to_decimal_safe(preview_price)
        lines.append("")
        lines.append("📊 <b>MARKET CONTEXT</b>")
        
        if side == "LONG":
            current_pnl = ((preview_price_dec - entry) / entry) * Decimal('100')
        else:
            current_pnl = ((entry - preview_price_dec) / entry) * Decimal('100')
        
        current_pnl_float = float(current_pnl.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))
        lines.append(f"Current: {_format_price_clean(preview_price)} ({_format_pnl_display(current_pnl_float)})")
    
    lines.append("")
    lines.append("📤 <i>Ready to publish this professional signal?</i>")
    lines.append(f"<i>This will be distributed to all {APP_NAME} users</i>")
    
    return "\n".join(lines)

# --- END OF PROFESSIONAL FIXED VERSION ---