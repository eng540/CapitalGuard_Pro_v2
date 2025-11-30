# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v7.0.0-PRO-CLOSE-PERCENT (With Take Profit Close Percentages)
# ✅ THE ENHANCEMENT:
#    1. Show close percentage for each target
#    2. Visual indicators for closure logic
#    3. Professional layout with essential information

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

log = logging.getLogger(__name__)

# --- Configuration ---
WEBAPP_SHORT_NAME = "terminal" 
BOT_USERNAME = "CapitalGuardProBot"

# --- PRO Icons & Styles ---
ICON_LONG = "🟢 LONG"
ICON_SHORT = "🔴 SHORT"
ICON_TARGET_HIT = "✅"
ICON_TARGET_WAIT = "⬜"
ICON_STOP = "🛑"
ICON_ENTRY = "🚪"
ICON_CLOSE = "💰"  # أيقونة جديدة للإغلاق

def _format_pnl(pnl: float) -> str:
    """تنسيق PnL بتصميم احترافي"""
    if pnl > 5: return f"🚀 +{pnl:.2f}%"
    if pnl > 0: return f"💚 +{pnl:.2f}%"
    if pnl < -5: return f"💀 {pnl:.2f}%"
    if pnl < 0: return f"🔻 {pnl:.2f}%"
    return "⚪ 0.00%"

def _extract_leverage(notes: str) -> str:
    """استخراج الرافعة المالية بشكل آمن"""
    if not notes: return "20x" 
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

def _draw_progress_bar(percent: float, length: int = 8) -> str:
    """شريط تقدم بصري مبسط"""
    percent = max(0, min(100, percent))
    filled = int(length * percent // 100)
    return "▓" * filled + "░" * (length - filled)

def _get_webapp_link(rec_id: int) -> str:
    """رابط WebApp آمن"""
    try:
        return f"https://t.me/{BOT_USERNAME}/{WEBAPP_SHORT_NAME}?startapp={rec_id}"
    except:
        return f"https://t.me/{BOT_USERNAME}"

# --- ✅ ENHANCED: Close Percentage Support ---

def _build_pro_header(rec: Recommendation) -> str:
    """هيدر احترافي مبسط"""
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
    """
    لوحة حالة ذكية - تبسيط في النشر الأولي
    """
    try:
        status = _get_attr(rec, 'status')
        live_price = getattr(rec, "live_price", None)
        entry = _to_decimal(_get_attr(rec, 'entry', 0))
        
        if status == RecommendationStatus.PENDING:
            return (
                f"⏳ <b>WAITING ENTRY</b>\n"
                f"Entry Price: <code>{_format_price(entry)}</code>"
            )
            
        if status == RecommendationStatus.CLOSED:
            exit_price = _to_decimal(_get_attr(rec, 'exit_price', 0))
            pnl = _pct(entry, exit_price, _get_attr(rec, 'side', 'LONG'))
            
            result_emoji = "🏆" if pnl > 0 else "📉"
            return (
                f"{result_emoji} <b>TRADE CLOSED</b>\n"
                f"Final Price: <code>{_format_price(exit_price)}</code>\n"
                f"Result: {_format_pnl(pnl)}"
            )

        # ✅ SIMPLIFIED: لا سعر حي في النشر الأولي
        if is_initial_publish:
            return "⚡ <b>TRADE ACTIVE</b>\nPosition opened successfully"
        
        # ✅ SMART: إظهار السعر الحي فقط في التحديثات اللاحقة
        if live_price:
            pnl = _pct(entry, live_price, _get_attr(rec, 'side', 'LONG'))
            
            # شريط التقدم البصري
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
                    f"Progress: <code>{bar}</code> {progress:.0f}%"
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
    """العناصر الاستراتيجية الأساسية فقط"""
    try:
        entry = _format_price(_get_attr(rec, 'entry', 0))
        sl = _format_price(_get_attr(rec, 'stop_loss', 0))
        
        # حساب المخاطرة بشكل آمن
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

def _build_targets_with_close_percent(rec: Recommendation) -> str:
    """
    ✅ ENHANCED: عرض الأهداف مع نسب الإغلاق
    """
    try:
        entry_price = _get_attr(rec, 'entry', 0)
        targets = _get_attr(rec, 'targets', [])
        targets_list = targets.values if hasattr(targets, 'values') else []
        
        if not targets_list:
            return "🎯 <b>Take Profit Targets:</b> No targets set"
        
        # تتبع الأهداف التي تم تحقيقها
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
            
            # ✅ ENHANCED: استخراج نسبة الإغلاق
            close_percent = target.get('close_percent', 0) if isinstance(target, dict) else 0
            close_text = ""
            
            if close_percent > 0:
                if close_percent == 100 and i == len(targets_list):
                    close_text = " [FULL CLOSE]"
                else:
                    close_text = f" [Close {close_percent:.0f}%]"
            
            if i in hit_targets:
                lines.append(f"{ICON_TARGET_HIT} <b>TP{i}: {_format_price(price)} (+{pct_value:.1f}%){close_text}</b>")
            else:
                lines.append(f"{ICON_TARGET_WAIT} TP{i}: <code>{_format_price(price)}</code> (+{pct_value:.1f}%){close_text}")
        
        # ✅ ENHANCED: إضافة ملخص لنسب الإغلاق
        total_close_percent = sum(
            target.get('close_percent', 0) if isinstance(target, dict) else 0 
            for target in targets_list
        )
        
        if total_close_percent > 0:
            lines.append(f"\n{ICON_CLOSE} <b>Close Summary:</b> {total_close_percent:.0f}% total position will be closed at targets")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error(f"Error building targets with close percent: {e}")
        return "🎯 <b>Take Profit Targets:</b> Error loading targets"

def _build_clean_timeline(rec: Recommendation) -> str:
    """جدول زمني نظيف بدون حدث الإنشاء"""
    try:
        if not rec.events:
            return ""
        
        # ✅ SIMPLIFIED: تصفية الأحداث المهمة فقط
        important_events = []
        for event in rec.events:
            event_type = getattr(event, 'event_type', '')
            # تجاهل حدث الإنشاء
            if event_type in ["CREATED", "RECOMMENDATION_CREATED"]:
                continue
            important_events.append(event)
        
        if not important_events:
            return ""
            
        # أخذ آخر حدثين مهمين فقط
        events_sorted = sorted(important_events, key=lambda e: getattr(e, 'event_timestamp', datetime.now()), reverse=True)[:2]
        lines = ["🕐 <b>Recent Activity:</b>"]
        
        for event in events_sorted:
            ts = getattr(event, 'event_timestamp', datetime.now()).strftime("%m/%d %H:%M")
            e_type = getattr(event, 'event_type', '').replace("_", " ").title()
            
            # تبسيط أسماء الأحداث
            if "Tp" in e_type and "Hit" in e_type:
                e_type = "🎯 Target Hit"
            elif "Sl" in e_type and "Hit" in e_type:
                e_type = "🛑 Stop Loss"
            elif "Partial" in e_type:
                # ✅ ENHANCED: إظهار نسبة الإغلاق في الأحداث الجزئية
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

def build_trade_card_text(rec: Recommendation, is_initial_publish: bool = False) -> str:
    """
    بناء بطاقة التوصية النهائية - مع نسب إغلاق الأهداف
    
    Args:
        rec: كيان التوصية
        is_initial_publish: هل هذه أول نشر؟ (لتبسيط السعر الحي)
    """
    try:
        DIVIDER = "────────────────"
        parts = []
        
        # 1. الهيدر الاحترافي
        parts.append(_build_pro_header(rec))
        parts.append("")
        
        # 2. لوحة الحالة الذكية
        parts.append(_build_smart_status(rec, is_initial_publish))
        parts.append(DIVIDER)
        
        # 3. الاستراتيجية الأساسية
        parts.append(_build_strategy_essentials(rec))
        parts.append(DIVIDER)
        
        # 4. ✅ ENHANCED: الأهداف مع نسب الإغلاق
        parts.append(_build_targets_with_close_percent(rec))
        
        # 5. ✅ SIMPLIFIED: الملاحظات فقط إذا كانت مفيدة
        notes = getattr(rec, 'notes', '')
        if notes and len(notes.strip()) > 10:
            clean_notes = re.sub(r'Lev:?\s*\d+x?\s*\|?', '', notes, flags=re.IGNORECASE).strip()
            if clean_notes:
                parts.append(DIVIDER)
                # تقليل طول الملاحظات إذا كانت طويلة
                short_notes = clean_notes[:100] + "..." if len(clean_notes) > 100 else clean_notes
                parts.append(f"📝 <b>Analysis:</b> {short_notes}")
        
        # 6. ✅ SIMPLIFIED: الجدول الزمني النظيف
        timeline = _build_clean_timeline(rec)
        if timeline:
            parts.append(DIVIDER)
            parts.append(timeline)
        
        # 7. الرابط التفاعلي
        link = _get_webapp_link(getattr(rec, 'id', 0))
        parts.append(f"\n🔍 <a href='{link}'><b>View Detailed Analytics & Charts</b></a>")

        return "\n".join(parts)
        
    except Exception as e:
        log.error(f"Error building pro trade card: {e}")
        return "📊 <b>TRADING SIGNAL</b>\n\n🚀 Active trading position\n\n🔍 <a href='https://t.me/CapitalGuardProBot'>View Details</a>"

# --- ✅ ENHANCED Review Text with Close Percent ---
def build_review_text_with_price(draft: Dict[str, Any], preview_price: Optional[float] = None) -> str:
    """
    نص مراجعة مع نسب الإغلاق
    """
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
        
        # ✅ ENHANCED: عرض الأهداف مع نسب الإغلاق
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
                
                text += f"TP{i}: <code>{_format_price(price)}</code> (+{pct_value:.1f}%){close_text}\n"
        
        text += f"\n📤 <i>Ready to publish to channels?</i>"
        
        return text
        
    except Exception as e:
        log.error(f"Error building review text: {e}")
        return "🛡️ <b>Confirm Trading Signal</b>\n\nReady to publish this signal to your channels?"

# --- SIMPLIFIED Portfolio View ---
class PortfolioViews:
    @staticmethod
    async def render_hub(update: Update, user_name: str, report: Dict[str, Any], active_count: int, watchlist_count: int, is_analyst: bool):
        """لوحة تحكم مبسطة"""
        try:
            from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            header = "📊 <b>CapitalGuard Portfolio</b>\nYour trading dashboard."
            
            # إحصائيات مبسطة
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