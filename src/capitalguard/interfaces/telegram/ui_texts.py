# --- START OF ENHANCED VERSION: src/capitalguard/interfaces/telegram/ui_texts.py ---
# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v66.0.0-ENHANCED (Smart Close Percent + Improved Events)
# 🚀 ENHANCEMENTS:
#    1. ✅ Close percentages for targets (TP1: 99,000 (+1.0%) | 20%)
#    2. ✅ Smart event processing with close percentages in timeline
#    3. ✅ Accurate terminology: BUY/SELL for Spot, LONG/SHORT for Futures
#    4. ✅ Dynamic bot username from settings
#    5. ✅ Enhanced event type handling with fallbacks
#    6. ✅ Breakeven detection in entry/stop line

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
ICON_CLOSE = "🎯"  # New icon for close percentages

def _format_pnl(pnl: float) -> str:
    """تنسيق الربح/الخسارة مع أيقونة مناسبة"""
    if pnl > 0:
        return f"🚀 {pnl:+.2f}%"
    elif pnl < 0:
        return f"🔻 {pnl:+.2f}%"
    return "⚡ 0.00%"

def _extract_leverage(notes: str) -> str:
    """استخراج الرافعة من الملاحظات"""
    if not notes: 
        return "20x"
    match = re.search(r'Lev:?\s*(\d+x?)', notes, re.IGNORECASE)
    return match.group(1) if match else "20x"

def _calculate_duration(rec: Recommendation) -> str:
    """حساب مدة الصفقة"""
    if not rec.created_at or not rec.closed_at: 
        return ""
    diff = rec.closed_at - rec.created_at
    hours, remainder = divmod(diff.seconds, 3600)
    minutes = remainder // 60
    if diff.days > 0: 
        return f"{diff.days}d {hours}h"
    return f"{hours}h {minutes}m"

def _rr(entry: Any, sl: Any, targets: List[Target]) -> str:
    """حساب نسبة المكافأة إلى المخاطرة"""
    try:
        entry_dec, sl_dec = _to_decimal(entry), _to_decimal(sl)
        if not targets: 
            return "-"
        first_target = targets[0]
        first_target_price = _to_decimal(_get_attr(first_target, 'price'))
        if not entry_dec.is_finite() or not sl_dec.is_finite() or not first_target_price.is_finite(): 
            return "-"
        risk = abs(entry_dec - sl_dec)
        if risk.is_zero(): 
            return "∞"
        reward = abs(first_target_price - entry_dec)
        ratio = reward / risk
        return f"1:{ratio:.1f}"
    except Exception: 
        return "-"

def _get_webapp_link(rec_id: int) -> str:
    """إنشاء رابط Web App ديناميكي"""
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'CapitalGuardBot')
    webapp_name = getattr(settings, 'TELEGRAM_WEBAPP_NAME', 'terminal')
    return f"https://t.me/{bot_username}/{webapp_name}?startapp={rec_id}"

def _get_target_close_percent(rec: Recommendation, target_num: int) -> int:
    """استخراج نسبة الإغلاق للهدف المحدد من الأحداث"""
    if not rec.events:
        return 0
    
    # البحث في أحداث TP_HIT
    for event in rec.events:
        event_type = getattr(event, 'event_type', '')
        if f"TP{target_num}_HIT" in event_type:
            event_data = getattr(event, 'event_data', {}) or {}
            return event_data.get('closed_percent', 0)
    
    # البحث في PARTIAL_CLOSE
    for event in rec.events:
        event_type = getattr(event, 'event_type', '')
        if "PARTIAL_CLOSE" in event_type:
            event_data = getattr(event, 'event_data', {}) or {}
            event_target = event_data.get('target_number')
            if event_target == target_num:
                return event_data.get('closed_percent', 0)
    
    return 0

def _is_breakeven(rec: Recommendation) -> bool:
    """الكشف إذا كان وقف الخسارة عند سعر الدخول (Breakeven)"""
    try:
        entry = _to_decimal(_get_attr(rec, 'entry'))
        stop_loss = _to_decimal(_get_attr(rec, 'stop_loss'))
        if entry and stop_loss:
            # إذا كان الفرق أقل من 0.1% يعتبر Breakeven
            difference = abs(entry - stop_loss) / entry * 100
            return difference < 0.1
    except:
        pass
    return False

def _build_header(rec: Recommendation) -> str:
    """بناء رأس البطاقة مع مصطلحات دقيقة"""
    symbol = _get_attr(rec.asset, 'value')
    side = _get_attr(rec.side, 'value')
    side_icon = ICON_LONG if side == "LONG" else ICON_SHORT
    
    raw_market = getattr(rec, 'market', 'Futures') or 'Futures'
    is_spot = "SPOT" in raw_market.upper()

    # ✅ مصطلحات دقيقة لكل نوع سوق
    if is_spot:
        side_display = "BUY" if side == "LONG" else "SELL"
        market_info = "💎 SPOT"
    else:
        side_display = side  # LONG أو SHORT
        lev_val = _extract_leverage(rec.notes)
        market_info = f"⚡ FUTURES ({lev_val})"

    # رابط تفاعلي للرمز
    link = _get_webapp_link(rec.id)
    return f"<a href='{link}'>#{symbol}</a> | {side_display} {side_icon} | {market_info}"

def _build_status_and_live(rec: Recommendation) -> str:
    """بناء قسم الحالة والسعر الحي"""
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

    # ACTIVE
    if live_price:
        entry = _get_attr(rec, 'entry')
        pnl = _pct(entry, live_price, _get_attr(rec, 'side'))
        return f"⚡ **ACTIVE** | Live: `{_format_price(live_price)}`\nPnL: {_format_pnl(pnl)}"
    
    return "⚡ **ACTIVE** (Loading...)"

def _build_compact_entry_stop(rec: Recommendation) -> str:
    """بناء سطر الدخول والوقف المضغوط"""
    entry = _format_price(_get_attr(rec, 'entry'))
    sl = _format_price(_get_attr(rec, 'stop_loss'))
    
    try:
        e_val = _to_decimal(_get_attr(rec, 'entry'))
        s_val = _to_decimal(_get_attr(rec, 'stop_loss'))
        risk_pct = abs((e_val - s_val) / e_val) * 100
        risk_str = f"{risk_pct:.1f}%"
    except: 
        risk_str = "-"
    
    targets = _get_attr(rec, 'targets', [])
    targets_list = targets.values if hasattr(targets, 'values') else []
    rr_str = _rr(e_val, s_val, targets_list)

    # ✅ إضافة مؤشر Breakeven إذا كان SL عند الدخول
    be_indicator = " (BE)" if _is_breakeven(rec) else ""
    
    return f"🚪 `{entry}` ➔ 🛑 `{sl}`{be_indicator} | Risk: {risk_str} (R:R {rr_str})"

def _build_targets_list(rec: Recommendation) -> str:
    """بناء قائمة الأهداف مع نسب الإغلاق"""
    entry_price = _get_attr(rec, 'entry')
    targets = _get_attr(rec, 'targets', [])
    targets_list = targets.values if hasattr(targets, 'values') else []
    
    hit_targets = set()
    if rec.events:
        for event in rec.events:
            event_type = getattr(event, 'event_type', '')
            if "TP" in event_type and "HIT" in event_type:
                try: 
                    # استخراج رقم الهدف من event_type مثل "TP1_HIT"
                    hit_targets.add(int(event_type[2]))
                except: 
                    pass

    lines = []
    for i, target in enumerate(targets_list, start=1):
        price = _get_attr(target, 'price')
        pct_value = _pct(entry_price, price, _get_attr(rec, 'side'))
        
        icon = ICON_TP if i in hit_targets else ICON_WAIT
        
        # ✅ استخراج نسبة الإغلاق للهدف
        close_percent = _get_target_close_percent(rec, i)
        
        # ✅ بناء السطر مع نسبة الإغلاق إذا كانت موجودة
        if close_percent > 0:
            line = f"{icon} TP{i}: `{_format_price(price)}` ({pct_value:+.1f}%) | {close_percent}%"
        else:
            line = f"{icon} TP{i}: `{_format_price(price)}` ({pct_value:+.1f}%)"
        
        lines.append(line)
    
    return "\n".join(lines) if lines else "🎯 No targets set"

def _build_timeline_compact(rec: Recommendation) -> str:
    """بناء تايم لاين مضغوط مع نسب الإغلاق"""
    if not rec.events: 
        return ""
    
    # ترتيب الأحداث من الأحدث إلى الأقدم
    events = sorted(rec.events, key=lambda e: e.event_timestamp, reverse=True)[:3]
    lines = []
    
    for event in events:
        ts = event.event_timestamp.strftime("%Y-%m-%d %H:%M")
        event_type = getattr(event, 'event_type', '')
        event_data = getattr(event, 'event_data', {}) or {}
        
        # ✅ استخراج نسبة الإغلاق من event_data
        close_percent = event_data.get('closed_percent', 0)
        close_suffix = f" | {close_percent}%" if close_percent > 0 else ""
        
        # ✅ معالجة محسنة لأنواع الأحداث
        display_text = ""
        
        if "TP" in event_type and "HIT" in event_type:
            # استخراج رقم الهدف من event_type
            tp_num = event_type[2] if len(event_type) > 2 else "?"
            display_text = f"TP{tp_num} Hit ✅{close_suffix}"
            
        elif event_type == "STOP_LOSS_HIT":
            display_text = "SL Hit 🛑"
            
        elif event_type == "ENTRY_FILLED":
            display_text = "Entry Filled 📥"
            
        elif event_type == "POSITION_CREATED":
            display_text = "Created 📡"
            
        elif "PARTIAL_CLOSE" in event_type:
            display_text = f"Partial Close {ICON_CLOSE}{close_suffix}"
            
        elif "CLOSE" in event_type and "HIT" not in event_type:
            display_text = f"Closed 🏁{close_suffix}"
            
        else:
            # تخطي الأحداث غير المعروفة
            continue
        
        if display_text:
            lines.append(f"▫️ `{ts}` {display_text}")
    
    return "\n".join(lines)

def _build_close_summary(rec: Recommendation) -> str:
    """بناء ملخص نسب الإغلاق الإجمالية"""
    if not rec.events or rec.status != RecommendationStatus.CLOSED:
        return ""
    
    total_closed = 0
    for event in rec.events:
        event_data = getattr(event, 'event_data', {}) or {}
        close_pct = event_data.get('closed_percent', 0)
        total_closed += close_pct
    
    if total_closed > 0:
        return f"📊 Total Closed: {total_closed}%"
    return ""

# --- Main Builder ---
def build_trade_card_text(rec: Recommendation) -> str:
    """الدالة الرئيسية لبناء نص البطاقة"""
    SEP = "──────────────"
    parts = []
    
    parts.append(_build_header(rec))
    parts.append(_build_status_and_live(rec))
    parts.append(SEP)
    parts.append(_build_compact_entry_stop(rec))
    parts.append(SEP)
    parts.append(_build_targets_list(rec))
    
    # ✅ إضافة ملخص الإغلاق للصفقات المغلقة
    if rec.status == RecommendationStatus.CLOSED:
        close_summary = _build_close_summary(rec)
        if close_summary:
            parts.append(SEP)
            parts.append(close_summary)
    
    if rec.notes:
        clean_notes = re.sub(r'Lev:?\s*\d+x?\s*\|?', '', rec.notes, flags=re.IGNORECASE).strip()
        if clean_notes:
            parts.append(SEP)
            parts.append(f"📝 {clean_notes}")
    
    timeline = _build_timeline_compact(rec)
    if timeline:
        parts.append(SEP)
        parts.append(timeline)

    return "\n".join(parts)

# --- Helpers for PnL Calculation (Preserved) ---
def _calculate_weighted_pnl(rec: Recommendation) -> float:
    """حساب الربح/الخسارة الموزون"""
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

# ... باقي الكود (PortfolioViews) يبقى كما هو