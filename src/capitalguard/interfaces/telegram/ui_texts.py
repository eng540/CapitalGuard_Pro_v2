# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v9.0.0-SMART-DESIGN (Auto Bot Name & Better Icons)
# ✅ IMPROVEMENTS:
#    1. Automatic bot username detection
#    2. Better waiting icons (⏳ for pending, 🚀 for next targets)
#    3. Professional icon sequencing
#    4. Clean, maintainable code

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

# --- Smart Configuration ---
def _get_bot_username() -> str:
    """الحصول على اسم البوت تلقائيًا من الإعدادات"""
    try:
        # محاولة الحصول من إعدادات البوت
        if hasattr(settings, 'TELEGRAM_BOT_USERNAME') and settings.TELEGRAM_BOT_USERNAME:
            return settings.TELEGRAM_BOT_USERNAME
        
        # أو من token إذا كان متوفرًا
        if hasattr(settings, 'TELEGRAM_BOT_TOKEN') and settings.TELEGRAM_BOT_TOKEN:
            # استخراج اسم البوت من token (مثال: "123456:ABC-DEF" -> اسم البوت)
            token_parts = settings.TELEGRAM_BOT_TOKEN.split(':')
            if len(token_parts) > 0:
                # هذا يعتمد على هيكل token الخاص بتليجرام
                bot_id = token_parts[0]
                # يمكن إضافة منطق لاستخراج اسم البوت إذا كان متوفرًا
                # للآن نستخدم قيمة افتراضية
                return f"CapitalGuardProBot"
        
        # قيمة افتراضية آمنة
        return "CapitalGuardProBot"
    except Exception as e:
        log.warning(f"Failed to get bot username automatically: {e}")
        return "CapitalGuardProBot"

# الحصول على اسم البوت مرة واحدة عند التحميل
BOT_USERNAME = _get_bot_username()
WEBAPP_SHORT_NAME = "terminal"

# --- Enhanced Icons & Styles ---
ICON_LONG = "🟢 LONG"
ICON_SHORT = "🔴 SHORT"
ICON_TARGET_HIT = "✅"      # هدف محقق
ICON_TARGET_NEXT = "🚀"     # الهدف التالي المنتظر
ICON_TARGET_WAIT = "⏳️"    # أهداف قيد الانتظار
ICON_STOP = "🛑"
ICON_ENTRY = "🚪"
ICON_CLOSE = "💰"
ICON_PROFIT = "💎"
ICON_LOSS = "🔻"

def _format_pnl(pnl: float) -> str:
    """تنسيق PnL مع أيقونات محسنة"""
    if pnl > 10: return f"🎯 +{pnl:.2f}%"
    if pnl > 5: return f"🚀 +{pnl:.2f}%"
    if pnl > 0: return f"💚 +{pnl:.2f}%"
    if pnl < -10: return f"💀 {pnl:.2f}%"
    if pnl < -5: return f"🔻 {pnl:.2f}%"
    if pnl < 0: return f"⚫ {pnl:.2f}%"
    return "⚪ 0.00%"

def _extract_leverage(notes: str) -> str:
    """استخراج الرافعة المالية بشكل آمن"""
    if not notes: return "20x" 
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

def _draw_progress_bar(percent: float, length: int = 8) -> str:
    """شريط تقدم بصري محسن"""
    percent = max(0, min(100, percent))
    filled = int(length * percent // 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"{bar} {percent:.0f}%"

def _get_webapp_link(rec_id: int) -> str:
    """رابط WebApp ذكي"""
    try:
        return f"https://t.me/{BOT_USERNAME}/{WEBAPP_SHORT_NAME}?startapp={rec_id}"
    except Exception as e:
        log.warning(f"WebApp link error: {e}")
        return f"https://t.me/{BOT_USERNAME}"

def _calculate_duration(rec: Recommendation) -> str:
    """حساب مدة التداول"""
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

# --- ✅ ENHANCED: Smart Target Icons Sequencing ---
def _get_target_icon(target_index: int, hit_targets: set, total_targets: int) -> str:
    """
    تحديد الأيقونة المناسبة للهدف بناءً على تسلسله
    """
    if target_index in hit_targets:
        return ICON_TARGET_HIT  # ✅ للهدف المحقق
    
    # البحث عن أول هدف لم يتحقق بعد
    next_unhit_target = None
    for i in range(1, total_targets + 1):
        if i not in hit_targets:
            next_unhit_target = i
            break
    
    if target_index == next_unhit_target:
        return ICON_TARGET_NEXT  # 🚀 للهدف التالي المنتظر
    else:
        return ICON_TARGET_WAIT  # ⏳️ للأهداف الأخرى المنتظرة

def _build_pro_header(rec: Recommendation) -> str:
    """هيدر احترافي مع اسم البوت التلقائي"""
    try:
        symbol = _get_attr(rec.asset, 'value', 'SYMBOL')
        side = _get_attr(rec.side, 'value', 'LONG')
        
        header_icon = "📈" if side == "LONG" else "📉"
        side_badge = ICON_LONG if side == "LONG" else ICON_SHORT
        
        raw_market = getattr(rec, 'market', 'Futures') or 'Futures'
        is_spot = "SPOT" in raw_market.upper()
        lev_info = "" if is_spot else f" • {_extract_leverage(getattr(rec, 'notes', ''))}"

        return f"{header_icon} <b>#{symbol}</b>  {side_badge}{lev_info}"
    except Exception:
        return "📊 <b>TRADING SIGNAL</b>"

def _build_smart_status(rec: Recommendation, is_initial_publish: bool = False) -> str:
    """لوحة حالة محسنة"""
    try:
        status = _get_attr(rec, 'status')
        live_price = getattr(rec, "live_price", None)
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        
        if status == RecommendationStatus.PENDING:
            return (
                f"⏳️ <b>WAITING ENTRY</b>\n"
                f"Entry Price: <code>{_format_price(entry)}</code>"
            )
            
        if status == RecommendationStatus.CLOSED:
            exit_price = _to_decimal(_get_attr(rec, 'exit_price', 0))
            pnl = _pct(entry, exit_price, _get_attr(rec, 'side', 'LONG'))
            duration = _calculate_duration(rec)
            dur_str = f" | ⏱️ {duration}" if duration else ""
            
            result_emoji = "🏆" if pnl > 0 else "📉"
            return (
                f"{result_emoji} <b>TRADE CLOSED</b>\n"
                f"Final Price: <code>{_format_price(exit_price)}</code>\n"
                f"Result: {_format_pnl(pnl)}{dur_str}"
            )

        if is_initial_publish:
            return "⚡ <b>TRADE ACTIVE</b>\nPosition opened successfully"
        
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
                    f"Price: <code>{_format_price(live_price)}</code>\n"
                    f"PnL: {_format_pnl(pnl)}\n"
                    f"Progress: {bar}"
                )
            
            return (
                f"⚡ <b>LIVE TRADING</b>\n"
                f"Price: <code>{_format_price(live_price)}</code>\n"
                f"PnL: {_format_pnl(pnl)}"
            )
        
        return "⚡ <b>TRADE ACTIVE</b>\nMonitoring markets..."
        
    except Exception:
        return "⚡ <b>TRADE ACTIVE</b>"

def _build_strategy_essentials(rec: Recommendation) -> str:
    """العناصر الاستراتيجية الأساسية"""
    try:
        entry = _format_price(_get_attr(rec, 'entry', 0))
        sl = _format_price(_get_attr(rec, 'stop_loss', 0))
        
        e_val = _to_decimal(_get_attr(rec, 'entry', 0))
        s_val = _to_decimal(_get_attr(rec, 'stop_loss', 0))
        risk_pct = abs((e_val - s_val) / e_val * 100) if e_val > 0 else 0
        
        return (
            f"{ICON_ENTRY} <b>Entry:</b> <code>{entry}</code>\n"
            f"{ICON_STOP} <b>Stop Loss:</b> <code>{sl}</code>\n"
            f"📊 <b>Risk:</b> {risk_pct:.1f}%"
        )
    except Exception:
        return f"{ICON_ENTRY} <b>Entry:</b> <code>N/A</code>\n{ICON_STOP} <b>Stop Loss:</b> <code>N/A</code>"

def _build_targets_with_smart_icons(rec: Recommendation) -> str:
    """
    ✅ ENHANCED: عرض الأهداف مع تسلسل أيقونات ذكي
    """
    try:
        entry_price = _get_attr(rec, 'entry', 0)
        targets = _get_attr(rec, 'targets', [])
        targets_list = targets.values if hasattr(targets, 'values') else []
        
        if not targets_list:
            return "🎯 <b>Take Profit Targets:</b> No targets set"
        
        # تحديد الأهداف المحققة
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

        lines = ["🎯 <b>Take Profit Targets:</b>"]
        
        for i, target in enumerate(targets_list, start=1):
            price = _get_attr(target, 'price', 0)
            pct_value = _pct(entry_price, price, _get_attr(rec, 'side', 'LONG'))
            
            # ✅ ENHANCED: استخدام التسلسل الذكي للأيقونات
            icon = _get_target_icon(i, hit_targets, len(targets_list))
            
            # نسبة الإغلاق
            close_percent = target.get('close_percent', 0) if isinstance(target, dict) else 0
            close_text = ""
            
            if close_percent > 0:
                if close_percent == 100 and i == len(targets_list):
                    close_text = " [FULL CLOSE]"
                else:
                    close_text = f" [Close {close_percent:.0f}%]"
            
            lines.append(f"{icon} TP{i}: <code>{_format_price(price)}</code> (+{pct_value:.1f}%){close_text}")
        
        # ملخص الإغلاق
        total_close_percent = sum(
            target.get('close_percent', 0) if isinstance(target, dict) else 0 
            for target in targets_list
        )
        
        if total_close_percent > 0:
            lines.append(f"\n{ICON_CLOSE} <b>Close Summary:</b> {total_close_percent:.0f}% total position will be closed at targets")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error(f"Error building targets with smart icons: {e}")
        return "🎯 <b>Take Profit Targets:</b> Error loading targets"

def _build_clean_timeline(rec: Recommendation) -> str:
    """جدول زمني نظيف"""
    try:
        if not rec.events:
            return ""
        
        important_events = []
        for event in rec.events:
            event_type = getattr(event, 'event_type', '')
            if event_type in ["CREATED", "RECOMMENDATION_CREATED"]:
                continue
            important_events.append(event)
        
        if not important_events:
            return ""
            
        events_sorted = sorted(important_events, key=lambda e: getattr(e, 'event_timestamp', datetime.now()), reverse=True)[:2]
        lines = ["🕐 <b>Recent Activity:</b>"]
        
        for event in events_sorted:
            ts = getattr(event, 'event_timestamp', datetime.now()).strftime("%m/%d %H:%M")
            e_type = getattr(event, 'event_type', '').replace("_", " ").title()
            
            if "Tp" in e_type and "Hit" in e_type:
                e_type = "🎯 Target Hit"
            elif "Sl" in e_type and "Hit" in e_type:
                e_type = "🛑 Stop Loss"
            elif "Partial" in e_type:
                event_data = getattr(event, 'event_data', {}) or {}
                closed_pct = event_data.get('closed_percent', 0)
                if closed_pct > 0:
                    e_type = f"💰 Close {closed_pct:.0f}%"
                else:
                    e_type = "💰 Partial Close"
            elif "Activated" in e_type:
                e_type = "⚡ Activated"
            elif "Closed" in e_type:
                e_type = "🏁 Closed"
                
            lines.append(f"▸ {ts} - {e_type}")
        
        return "\n".join(lines)
    except Exception:
        return ""

# --- ✅ COMPLETE MAIN FUNCTION ---
def build_trade_card_text(rec: Recommendation, is_initial_publish: bool = False) -> str:
    """بناء بطاقة التوصية النهائية مع كل التحسينات"""
    try:
        DIVIDER = "────────────────"
        parts = []
        
        parts.append(_build_pro_header(rec))
        parts.append("")
        parts.append(_build_smart_status(rec, is_initial_publish))
        parts.append(DIVIDER)
        parts.append(_build_strategy_essentials(rec))
        parts.append(DIVIDER)
        
        # ✅ ENHANCED: استخدام الأيقونات الذكية
        parts.append(_build_targets_with_smart_icons(rec))
        
        notes = getattr(rec, 'notes', '')
        if notes and len(notes.strip()) > 10:
            clean_notes = re.sub(r'Lev:?\s*\d+x?\s*\|?', '', notes, flags=re.IGNORECASE).strip()
            if clean_notes:
                parts.append(DIVIDER)
                short_notes = clean_notes[:100] + "..." if len(clean_notes) > 100 else clean_notes
                parts.append(f"📝 <b>Analysis:</b> {short_notes}")
        
        timeline = _build_clean_timeline(rec)
        if timeline:
            parts.append(DIVIDER)
            parts.append(timeline)
        
        link = _get_webapp_link(getattr(rec, 'id', 0))
        parts.append(f"\n🔍 <a href='{link}'><b>View Detailed Analytics & Charts</b></a>")

        return "\n".join(parts)
        
    except Exception as e:
        log.error(f"Error building enhanced trade card: {e}")
        return "📊 <b>TRADING SIGNAL</b>\n\n🚀 Active trading position\n\n🔍 <a href='https://t.me/CapitalGuardProBot'>View Details</a>"

# --- ✅ ENHANCED REVIEW FUNCTION ---
def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float] = None) -> str:
    """نص مراجعة محسن"""
    try:
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
            text += f"\n🎯 <b>Take Profit Targets:</b>\n"
            for i, target in enumerate(targets, start=1):
                price = _to_decimal(target.get('price', 0))
                close_percent = target.get('close_percent', 0)
                pct_value = _pct(entry, price, side)
                
                close_text = ""
                if close_percent > 0:
                    if close_percent == 100 and i == len(targets):
                        close_text = " [FULL CLOSE]"
                    else:
                        close_text = f" [Close {close_percent:.0f}%]"
                
                # ✅ استخدام أيقونات المراجعة أيضًا
                icon = "⏳️" if i == 1 else "🚀" if i == 2 else "🎯"
                text += f"{icon} TP{i}: <code>{_format_price(price)}</code> (+{pct_value:.1f}%){close_text}\n"
        
        text += f"\n📤 <i>Ready to publish to channels?</i>"
        
        return text
        
    except Exception as e:
        log.error(f"Error building review text: {e}")
        return "🛡️ <b>Confirm Trading Signal</b>\n\nReady to publish this signal to your channels?"

# --- PORTFOLIO CLASS (باقي الكود كما هو) ---
class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any], active_count: int, watchlist_count: int, is_analyst: bool):
        """لوحة تحكم محسنة"""
        try:
            from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            header = "📊 <b>CapitalGuard Portfolio</b>\nYour trading dashboard."
            
            stats_card = (
                "────────────────\n"
                "📈 <b>Portfolio Summary</b>\n"
                f"• Active Trades: <b>{active_count}</b>\n"
                f"• Watchlist: <b>{watchlist_count}</b>\n"
                "────────────────\n"
                "<b>Quick Access:</b>"
            )
            
            ns = CallbackNamespace.MGMT
            keyboard = [
                [InlineKeyboardButton(f"🚀 Active ({active_count})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
                [InlineKeyboardButton(f"👁️ Watchlist ({watchlist_count})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
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