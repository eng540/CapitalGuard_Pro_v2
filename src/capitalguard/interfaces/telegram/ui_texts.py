# --- START OF OPTIMIZED DESIGN: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v12.0.0-PROFESSIONAL
# ✅ DESIGN PHILOSOPHY:
#     1. Information Hierarchy: Most important first (PnL → Price → Plan)
#     2. Context-Aware: Different info based on trade status/user level
#     3. Visual Clarity: Clean spacing, minimal icons, clear sections
#     4. Progressive Disclosure: Essential info first, details on demand
#     5. Zero Clutter: Remove redundant/marketing messages

from __future__ import annotations
import logging
import re
from typing import Optional, Dict, Any, Tuple
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.error import BadRequest

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.interfaces.telegram.helpers import _get_attr, _to_decimal, _pct

log = logging.getLogger(__name__)

# ============================================================================
# CORE: SMART DATA ENGINE
# ============================================================================

class TradingCardEngine:
    """Smart engine for context-aware trade card generation"""
    
    # Priority levels for information display
    INFO_PRIORITY = {
        'pnl': 100,          # Most important
        'status': 90,
        'price': 80,
        'direction': 70,
        'entry_stop': 60,
        'targets': 50,
        'risk': 40,
        'leverage': 30,
        'timeline': 20,
        'notes': 10,         # Least important
    }
    
    @staticmethod
    async def get_display_data(rec: Recommendation) -> Dict[str, Any]:
        """Gather all data with smart prioritization"""
        # Core data
        symbol = _get_attr(rec.asset, 'value', 'SYMBOL').upper()
        side = _get_attr(rec.side, 'value', 'LONG')
        status = _get_attr(rec, 'status')
        
        # Prices (with safe Decimal handling)
        entry = TradingCardEngine._safe_decimal(_get_attr(rec, 'entry', 0))
        stop = TradingCardEngine._safe_decimal(_get_attr(rec, 'stop_loss', 0))
        
        # Live price (async)
        market = getattr(rec, 'market', 'Futures') or 'Futures'
        live_price = await TradingCardEngine._get_live_price_safe(symbol, market)
        
        # Calculate key metrics
        current_price = live_price if live_price else entry
        pnl_data = TradingCardEngine._calculate_pnl_smart(rec, entry, side, current_price)
        
        # Risk metrics
        risk_pct = TradingCardEngine._calculate_risk(entry, stop)
        
        # Determine context
        context = TradingCardEngine._determine_context(status, pnl_data['total_pnl'])
        
        return {
            'symbol': symbol,
            'side': side,
            'status': status,
            'context': context,
            'entry': entry,
            'stop': stop,
            'current_price': current_price,
            'live_price': live_price,
            'pnl_data': pnl_data,
            'risk_pct': risk_pct,
            'leverage': TradingCardEngine._extract_leverage_smart(rec, market),
            'market': market,
            'has_targets': bool(_get_attr(rec, 'targets', [])),
            'has_events': bool(getattr(rec, 'events', [])),
            'rec_id': getattr(rec, 'id', 0),
        }
    
    @staticmethod
    def _safe_decimal(value: Any) -> Decimal:
        """Ultra-safe decimal conversion"""
        try:
            if isinstance(value, Decimal):
                return value
            if value is None:
                return Decimal('0')
            return Decimal(str(float(value)))
        except:
            return Decimal('0')
    
    @staticmethod
    async def _get_live_price_safe(symbol: str, market: str) -> Optional[Decimal]:
        """Safe live price fetching with fallback"""
        try:
            from capitalguard.infrastructure.core_engine import core_cache
            
            cache_key = f"price:{market.upper()}:{symbol}"
            price = await core_cache.get(cache_key)
            
            if price:
                return TradingCardEngine._safe_decimal(price)
            
            # Cross-market fallback (Futures ↔ Spot)
            alt_market = "SPOT" if market == "Futures" else "Futures"
            alt_key = f"price:{alt_market}:{symbol}"
            price = await core_cache.get(alt_key)
            
            return TradingCardEngine._safe_decimal(price) if price else None
        except:
            return None
    
    @staticmethod
    def _calculate_pnl_smart(rec: Recommendation, entry: Decimal, side: str, current_price: Decimal) -> Dict[str, Any]:
        """Smart PnL calculation focusing on what matters"""
        try:
            # Realized PnL from events
            realized_pnl = Decimal('0')
            closed_pct = Decimal('0')
            partial_closes = []
            
            if rec.events:
                for event in rec.events:
                    if "PARTIAL_CLOSE" in getattr(event, 'event_type', ''):
                        event_data = getattr(event, 'event_data', {}) or {}
                        close_price = event_data.get('price')
                        close_pct = event_data.get('closed_percent', 0)
                        
                        if close_price and close_pct:
                            close_price_dec = TradingCardEngine._safe_decimal(close_price)
                            profit = _pct(entry, close_price_dec, side)
                            realized_pnl += profit * Decimal(str(close_pct)) / Decimal('100')
                            closed_pct += Decimal(str(close_pct))
            
            # Current unrealized PnL
            current_pnl = _pct(entry, current_price, side) if entry > Decimal('0') else Decimal('0')
            
            # Total PnL (realized + unrealized weighted by open position)
            open_pct = Decimal('100') - closed_pct
            total_pnl = realized_pnl + (current_pnl * open_pct / Decimal('100'))
            
            return {
                'current': float(current_pnl.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'realized': float(realized_pnl.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'total': float(total_pnl.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'closed_pct': float(closed_pct),
                'open_pct': float(open_pct),
            }
        except:
            return {'current': 0.0, 'realized': 0.0, 'total': 0.0, 'closed_pct': 0.0, 'open_pct': 100.0}
    
    @staticmethod
    def _calculate_risk(entry: Decimal, stop: Decimal) -> float:
        """Clean risk calculation"""
        try:
            if entry == Decimal('0'):
                return 0.0
            risk = abs(entry - stop) / entry * Decimal('100')
            return float(risk.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))
        except:
            return 0.0
    
    @staticmethod
    def _extract_leverage_smart(rec: Recommendation, market: str) -> str:
        """Smart leverage display"""
        if "SPOT" in market.upper():
            return ""
        
        notes = getattr(rec, 'notes', '')
        if not notes:
            return "20x"
        
        match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
        return match.group(1) if match else "20x"
    
    @staticmethod
    def _determine_context(status: RecommendationStatus, pnl: float) -> str:
        """Determine the context for information display"""
        if status == RecommendationStatus.PENDING:
            return "pending"
        elif status == RecommendationStatus.CLOSED:
            return "winner" if pnl > 0 else "stopped"
        else:  # ACTIVE
            return "active"

# ============================================================================
# VISUAL DESIGN SYSTEM
# ============================================================================

class VisualDesignSystem:
    """Clean, minimalist visual design system"""
    
    # Status-based visual mapping
    STATUS_DESIGN = {
        'pending': {
            'icon': '⏳',
            'title': 'PENDING ORDER',
            'color': '🟡',
            'priority': ['price', 'entry', 'status']
        },
        'active': {
            'icon': '📊',
            'title': 'ACTIVE TRADE',
            'color': '🔵',
            'priority': ['pnl', 'price', 'entry_stop', 'targets']
        },
        'winner': {
            'icon': '🏆',
            'title': 'TRADE CLOSED',
            'color': '🟢',
            'priority': ['pnl', 'price', 'entry_stop']
        },
        'stopped': {
            'icon': '🛑',
            'title': 'POSITION CLOSED',
            'color': '🔴',
            'priority': ['pnl', 'price', 'entry_stop']
        }
    }
    
    # Side-based design
    SIDE_DESIGN = {
        'LONG': {'icon': '↗️', 'label': 'LONG', 'color': '🟢'},
        'SHORT': {'icon': '↘️', 'label': 'SHORT', 'color': '🔴'}
    }
    
    @staticmethod
    def format_currency(value: Any) -> str:
        """Minimalist currency formatting"""
        try:
            num = float(value)
            if num == 0:
                return "0"
            elif abs(num) >= 10000:
                return f"${num/1000:.0f}K"
            elif abs(num) >= 1000:
                return f"${num:,.0f}"
            elif abs(num) >= 1:
                return f"${num:.2f}"
            elif abs(num) >= 0.01:
                return f"${num:.4f}"
            else:
                return f"${num:.6f}"
        except:
            return str(value)
    
    @staticmethod
    def format_pnl(pnl: float) -> Tuple[str, str]:
        """Clean PnL formatting with emphasis"""
        if pnl > 10:
            return ("🚀", f"+{pnl:.1f}%")
        elif pnl > 5:
            return ("💰", f"+{pnl:.1f}%")
        elif pnl > 0:
            return ("📈", f"+{pnl:.1f}%")
        elif pnl < -10:
            return ("💀", f"{pnl:.1f}%")
        elif pnl < -5:
            return ("📉", f"{pnl:.1f}%")
        elif pnl < 0:
            return ("🔻", f"{pnl:.1f}%")
        else:
            return ("⚪", "0.0%")
    
    @staticmethod
    def format_compact_line(label: str, value: str, icon: str = "") -> str:
        """Create compact information line"""
        if icon:
            return f"{icon} <b>{label}:</b> {value}"
        return f"<b>{label}:</b> {value}"
    
    @staticmethod
    def create_section_header(title: str) -> str:
        """Clean section header"""
        return f"\n<b>{title}</b>"
    
    @staticmethod
    def create_divider() -> str:
        """Minimal divider"""
        return "─"

# ============================================================================
# MAIN CARD BUILDER - OPTIMIZED
# ============================================================================

async def build_trade_card_text(rec: Recommendation, bot_username: str, 
                               is_initial_publish: bool = False, 
                               detail_level: str = "normal") -> str:
    """
    Optimized trade card with information hierarchy
    detail_level: "minimal" | "normal" | "detailed"
    """
    try:
        # Get smart data
        data = await TradingCardEngine.get_display_data(rec)
        design = VisualDesignSystem.STATUS_DESIGN[data['context']]
        side_design = VisualDesignSystem.SIDE_DESIGN[data['side']]
        
        lines = []
        
        # ===== HEADER: Most Critical Information =====
        pnl_icon, pnl_text = VisualDesignSystem.format_pnl(data['pnl_data']['total'])
        
        # Line 1: PnL + Status (MOST IMPORTANT)
        status_display = design['title']
        if data['status'] == RecommendationStatus.ACTIVE and data['pnl_data']['closed_pct'] > 0:
            status_display = f"ACTIVE • {data['pnl_data']['closed_pct']:.0f}% secured"
        
        lines.append(f"{pnl_icon} <b>{pnl_text}</b> • {status_display}")
        
        # Line 2: Symbol + Price + Direction
        price_display = VisualDesignSystem.format_currency(data['current_price'])
        lines.append(f"#{data['symbol']} {price_display} • {side_design['icon']} {data['side']}")
        
        # Add leverage only if not spot
        if data['leverage'] and detail_level != "minimal":
            lines[-1] += f" • {data['leverage']}"
        
        # ===== CORE TRADING INFORMATION =====
        lines.append(VisualDesignSystem.create_divider())
        
        # Entry & Stop (Essential for all trades)
        entry_display = VisualDesignSystem.format_currency(data['entry'])
        stop_display = VisualDesignSystem.format_currency(data['stop'])
        
        lines.append(f"📍 <b>Entry:</b> {entry_display}")
        lines.append(f"🛑 <b>Stop:</b> {stop_display}")
        
        # Risk only if meaningful (>1%)
        if data['risk_pct'] >= 1.0 and detail_level != "minimal":
            lines.append(f"⚠️ <b>Risk:</b> {data['risk_pct']:.1f}%")
        
        # ===== TARGETS (If available and relevant) =====
        targets = _get_attr(rec, 'targets', [])
        target_list = targets.values if hasattr(targets, 'values') else []
        
        if target_list and detail_level != "minimal":
            lines.append(VisualDesignSystem.create_divider())
            
            # Show only next target for minimal/normal, all for detailed
            max_targets = 1 if detail_level == "normal" else len(target_list)
            
            # Determine hit targets
            hit_targets = set()
            if rec.events:
                for event in rec.events:
                    if "TP_HIT" in str(getattr(event, 'event_type', '')):
                        try:
                            target_num = int(''.join(filter(str.isdigit, str(event.event_type))))
                            hit_targets.add(target_num)
                        except:
                            pass
            
            for i, target in enumerate(target_list[:max_targets], 1):
                price = TradingCardEngine._safe_decimal(_get_attr(target, 'price', 0))
                profit = _pct(data['entry'], price, data['side'])
                close_pct = target.get('close_percent', 0) if isinstance(target, dict) else getattr(target, 'close_percent', 0)
                
                icon = "✅" if i in hit_targets else "🎯" if i == 1 else "•"
                price_fmt = VisualDesignSystem.format_currency(price)
                
                if i in hit_targets:
                    line = f"{icon} TP{i}: <s>{price_fmt}</s> (+{profit:.1f}%)"
                else:
                    line = f"{icon} TP{i}: {price_fmt} (+{profit:.1f}%)"
                
                if close_pct > 0:
                    line += f" ({close_pct:.0f}%)"
                
                lines.append(line)
            
            # Indicate more targets if available
            if len(target_list) > max_targets and detail_level == "normal":
                lines.append(f"• +{len(target_list) - 1} more targets")
        
        # ===== TIMELINE (Only for detailed view or if recent events) =====
        if detail_level == "detailed" and data['has_events']:
            timeline = _build_smart_timeline(rec)
            if timeline:
                lines.append(VisualDesignSystem.create_divider())
                lines.append(timeline)
        
        # ===== CALL TO ACTION (Context-aware) =====
        lines.append(VisualDesignSystem.create_divider())
        
        safe_username = bot_username.replace("@", "")
        rec_id = data['rec_id']
        
        if detail_level == "minimal":
            # Minimal CTA
            link = f"https://t.me/{safe_username}/{WEBAPP_SHORT_NAME}?startapp={rec_id}"
            lines.append(f"🔍 <a href='{link}'><b>View details</b></a>")
        else:
            # Full CTA with context
            if data['status'] == RecommendationStatus.ACTIVE:
                cta_text = "Monitor live charts & analytics"
            elif data['status'] == RecommendationStatus.CLOSED:
                cta_text = "Review trade analysis"
            else:
                cta_text = "Track order status"
            
            link = f"https://t.me/{safe_username}/{WEBAPP_SHORT_NAME}?startapp={rec_id}"
            lines.append(f"📊 <a href='{link}'><b>{cta_text}</b></a>")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error(f"Optimized card error: {e}", exc_info=True)
        # Fallback minimal card
        return f"📊 <b>Trading Signal</b>\n🔍 <a href='https://t.me/{bot_username}'>View in app</a>"

def _build_smart_timeline(rec: Recommendation) -> str:
    """Smart timeline showing only recent significant events"""
    if not rec.events:
        return ""
    
    # Filter for significant events in last 24 hours
    cutoff = datetime.now() - timedelta(hours=24)
    significant = []
    
    for event in rec.events[-3:]:  # Only last 3 events
        event_time = getattr(event, 'event_timestamp', None)
        event_type = getattr(event, 'event_type', '')
        
        if not event_time or event_time < cutoff:
            continue
        
        # Map to human-readable format
        if "TP_HIT" in event_type:
            desc = "Target hit"
        elif "SL_HIT" in event_type:
            desc = "Stop loss"
        elif "PARTIAL_CLOSE" in event_type:
            desc = "Partial close"
        elif "ACTIVATED" in event_type:
            desc = "Activated"
        else:
            desc = event_type.replace('_', ' ').title()
        
        time_str = event_time.strftime("%H:%M")
        significant.append(f"• {time_str} {desc}")
    
    if significant:
        return "📝 Recent:\n" + "\n".join(significant)
    return ""

# ============================================================================
# PORTFOLIO VIEWS - OPTIMIZED
# ============================================================================

class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any], 
                        active_count: int, watchlist_count: int, is_analyst: bool):
        """Clean portfolio dashboard"""
        try:
            from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace
            
            # Minimal header
            lines = [
                f"📊 <b>Portfolio Overview</b>",
                f"Hi {user_name.split()[0] if ' ' in user_name else user_name}",
                "",
            ]
            
            # Only show essential metrics
            win_rate = report.get('win_rate_pct', 'N/A')
            total_pnl = report.get('total_pnl_pct', '0%')
            
            lines.extend([
                "─",
                f"🏆 <b>Win Rate:</b> {win_rate}",
                f"💰 <b>Total PnL:</b> {total_pnl}",
                "",
                f"📈 <b>Active:</b> {active_count}",
                f"👁️ <b>Watching:</b> {watchlist_count}",
                "─",
            ])
            
            # Clean navigation
            ns = CallbackNamespace.MGMT
            buttons = []
            
            if active_count > 0:
                buttons.append([InlineKeyboardButton(
                    f"📈 Active ({active_count})", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1)
                )])
            
            if watchlist_count > 0:
                buttons.append([InlineKeyboardButton(
                    f"👁️ Watchlist ({watchlist_count})", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1)
                )])
            
            buttons.append([InlineKeyboardButton(
                "📜 History", 
                callback_data=CallbackBuilder.create(ns, "show_list", "history", 1)
            )])
            
            if is_analyst:
                buttons.append([InlineKeyboardButton(
                    "🔧 Tools", 
                    callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1)
                )])
            
            buttons.append([InlineKeyboardButton(
                "🔄 Refresh", 
                callback_data=CallbackBuilder.create(ns, "hub")
            )])
            
            # Send/update
            text = "\n".join(lines)
            keyboard = InlineKeyboardMarkup(buttons)
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text=text, reply_markup=keyboard, 
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            else:
                await update.effective_message.reply_text(
                    text=text, reply_markup=keyboard, 
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
                
        except BadRequest:
            pass
        except Exception as e:
            log.error(f"Portfolio error: {e}")

# ============================================================================
# REVIEW SCREEN - CLEAN
# ============================================================================

def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float] = None) -> str:
    """Clean review screen focusing on essentials"""
    asset = draft.get("asset", "SYMBOL").upper()
    side = draft.get("side", "LONG")
    entry = TradingCardEngine._safe_decimal(draft.get("entry", 0))
    sl = TradingCardEngine._safe_decimal(draft.get("stop_loss", 0))
    
    side_icon = "🟢" if side == "LONG" else "🔴"
    
    lines = [
        f"📤 <b>Confirm Signal</b>",
        "",
        f"{side_icon} <b>#{asset}</b> • {side}",
        f"📍 Entry: {VisualDesignSystem.format_currency(entry)}",
        f"🛑 Stop: {VisualDesignSystem.format_currency(sl)}",
    ]
    
    # Risk calculation
    risk_pct = TradingCardEngine._calculate_risk(entry, sl)
    if risk_pct > 0:
        lines.append(f"⚠️ Risk: {risk_pct:.1f}%")
    
    # Targets summary only
    targets = draft.get("targets", [])
    if targets:
        lines.append("")
        lines.append("🎯 Targets:")
        
        # Show only first 2 targets in review
        for i, target in enumerate(targets[:2], 1):
            price = TradingCardEngine._safe_decimal(target.get('price', 0))
            close_pct = target.get('close_percent', 0)
            
            price_fmt = VisualDesignSystem.format_currency(price)
            close_tag = f" ({close_pct:.0f}%)" if close_pct > 0 else ""
            
            lines.append(f"TP{i}: {price_fmt}{close_tag}")
        
        if len(targets) > 2:
            lines.append(f"... +{len(targets) - 2} more")
    
    lines.append("")
    lines.append("<i>Ready to publish?</i>")
    
    return "\n".join(lines)

# --- END OF OPTIMIZED DESIGN ---