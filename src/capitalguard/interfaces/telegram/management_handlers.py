# src/capitalguard/interfaces/telegram/management_handlers.py (v18.0 - Unified Position Management)
"""
نظام إدارة موحد للوحة التحكم يدعم كلاً من التوصيات والصفقات الشخصية
Unified position management system supporting both recommendations and personal trades
"""

import logging
from time import time
from typing import Optional, Dict, Any

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from .helpers import get_service, unit_of_work, parse_tail_int, parse_cq_parts
from .keyboards import (
    analyst_control_panel_keyboard,
    analyst_edit_menu_keyboard,
    confirm_close_keyboard,
    build_open_recs_keyboard,
    build_exit_strategy_keyboard,
    build_close_options_keyboard,
    build_user_trade_control_keyboard,
)
from .ui_texts import build_trade_card_text
from .parsers import parse_number, parse_targets_list
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"

# ==================== دوال مساعدة محسنة ====================

async def _send_or_edit_position_panel(
    context: ContextTypes.DEFAULT_TYPE, 
    db_session, 
    chat_id: int, 
    message_id: int, 
    position_id: int, 
    user_id: int,
    position_type: str = 'rec'
):
    """إرسال أو تعديل لوحة التحكم للصفقة/التوصية"""
    trade_service = get_service(context, "trade_service", TradeService)
    
    # الحصول على تفاصيل الصفقة/التوصية
    if position_type == 'rec':
        position = trade_service.get_recommendation_for_user(db_session, position_id, str(user_id))
    else:  # 'trade'
        position = trade_service.get_user_trade_details(db_session, position_id, str(user_id))
        if position:
            # تحويل إلى كيان RecommendationEntity للتوافق
            from capitalguard.domain.entities import RecommendationEntity, RecommendationStatus as RecStatusEntity
            from capitalguard.domain.value_objects import Symbol, Side, Price, Targets
            position = RecommendationEntity(
                id=position['id'],
                asset=Symbol(position['asset']),
                side=Side(position['side']),
                entry=Price(position['entry']),
                stop_loss=Price(position['stop_loss']),
                targets=Targets(position['targets']),
                status=RecStatusEntity.ACTIVE if position['status'] == 'OPEN' else RecStatusEntity.CLOSED,
                market="Futures",
                user_id=str(user_id)
            )
            setattr(position, 'is_user_trade', True)
            setattr(position, 'current_pnl', position.get('current_pnl'))
            setattr(position, 'realized_pnl', position.get('realized_pnl'))
    
    if not position:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id, 
                text="❌ الصفقة غير موجودة أو لا تملك صلاحية الوصول."
            )
        except Exception: 
            pass
        return
    
    # تحديث السعر الحي
    price_service = get_service(context, "price_service", PriceService)
    try:
        market = getattr(position, 'market', 'Futures')
        live_price = await price_service.get_cached_price(
            position.asset.value, market, force_refresh=True
        )
        if live_price: 
            setattr(position, "live_price", live_price)
    except Exception as e:
        log.debug(f"خطأ في جلب السعر الحي: {e}")
    
    # بناء النص والعرض
    text = build_trade_card_text(position)
    
    # تحديد لوحة المفاتيح المناسبة
    is_trade = getattr(position, 'is_user_trade', False)
    if is_trade:
        # لوحة تحكم صفقة المستخدم
        keyboard = build_user_trade_control_keyboard(position_id)
    else:
        # لوحة تحكم توصية المحلل
        keyboard = analyst_control_panel_keyboard(position) if position.status != RecommendationStatus.CLOSED else None

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, 
            message_id=message_id, 
            text=text, 
            reply_markup=keyboard, 
            parse_mode=ParseMode.HTML, 
            disable_web_page_preview=True
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"فشل تعديل الرسالة للوحة التحكم: {e}")

async def _send_or_edit_strategy_menu(
    context: ContextTypes.DEFAULT_TYPE, 
    db_session, 
    chat_id: int, 
    message_id: int, 
    rec_id: int, 
    user_id: int
):
    """إرسال أو تعديل قائمة استراتيجية الخروج (للتوصيات فقط)"""
    trade_service = get_service(context, "trade_service", TradeService)
    rec = trade_service.get_recommendation_for_user(db_session, rec_id, str(user_id))
    
    if not rec:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id, 
                text="❌ التوصية غير موجودة."
            )
        except Exception: 
            pass
        return
    
    strategy_text = "الإغلاق عند الهدف الأخير" if rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP else "الإغلاق اليدوي فقط"
    profit_stop_text = f"{rec.profit_stop_price:g}" if getattr(rec, "profit_stop_price", None) is not None else "غير مضبوط"
    
    text = (f"<b>الإشارة #{rec.id} | {rec.asset.value}</b>\n"
            f"------------------------------------\n"
            f"<b>إدارة استراتيجية الخروج</b>\n\n"
            f"<b>- استراتيجية الإغلاق الحالية:</b> {strategy_text}\n"
            f"<b>- وقف الربح الحالي:</b> {profit_stop_text}\n\n"
            f"اختر الإجراء المناسب:")
    
    keyboard = build_exit_strategy_keyboard(rec)
    
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, 
            message_id=message_id, 
            text=text, 
            reply_markup=keyboard, 
            parse_mode=ParseMode.HTML
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"فشل تعديل قائمة الاستراتيجية للتوصية #{rec_id}: {e}")

# ==================== معالجات الاستدعاء الموحدة ====================

@unit_of_work
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """معالج موحد لعرض لوحة التحكم للصفقات والتوصيات"""
    query = update.callback_query
    await query.answer()
    
    # تحليل بيانات الاستدعاء (مثال: ['pos', 'show_panel', 'rec', '189'])
    parts = parse_cq_parts(query.data)
    if len(parts) < 4:
        await query.edit_message_text("❌ طلب غير صالح.")
        return

    position_type = parts[2]  # 'rec' أو 'trade'
    position_id = int(parts[3])
    user_id = query.from_user.id

    await _send_or_edit_position_panel(
        context, db_session, 
        query.message.chat_id, query.message.message_id, 
        position_id, user_id, position_type
    )

@unit_of_work
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """التنقل بين الصفقات المفتوحة (توصيات وصفقات شخصية)"""
    query = update.callback_query
    await query.answer()
    
    page = parse_tail_int(query.data) or 1
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    
    # الحصول على جميع الصفقات المفتوحة
    items = trade_service.get_open_positions_for_user(db_session, str(query.from_user.id))
    
    if not items:
        await query.edit_message_text(text="✅ لا توجد لديك صفقات مفتوحة.")
        return
    
    keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
    await query.edit_message_text(
        text="<b>📊 صفقاتك المفتوحة</b>\nاختر صفقة للتحكم:",
        reply_markup=keyboard, 
        parse_mode=ParseMode.HTML
    )

# ==================== معالجات المحللين (للتوصيات فقط) ====================

@require_active_user
@require_analyst_user
@unit_of_work
async def cancel_pending_rec_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """إلغاء توصية معلقة"""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    
    if not rec_id:
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    
    await query.answer("جاري إلغاء التوصية...")
    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        updated_rec = await trade_service.cancel_pending_recommendation_manual(
            rec_id, str(query.from_user.id), db_session=db_session
        )
        await query.edit_message_text(
            f"✅ التوصية #{updated_rec.id} ({updated_rec.asset.value}) تم إلغاؤها بنجاح."
        )
    except ValueError as e:
        await query.answer(str(e), show_alert=True)
        await _send_or_edit_position_panel(
            context, db_session, 
            query.message.chat_id, query.message.message_id, 
            rec_id, query.from_user.id, 'rec'
        )

@require_active_user
@require_analyst_user
async def show_close_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة خيارات الإغلاق"""
    query = update.callback_query
    await query.answer()
    
    rec_id = parse_tail_int(query.data)
    if not rec_id:
        return
    
    text = f"{query.message.text}\n\n--- \n<b>اختر طريقة الإغلاق:</b>"
    keyboard = build_close_options_keyboard(rec_id)
    
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@require_active_user
@require_analyst_user
async def close_with_manual_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب سعر الإغلاق يدوياً"""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    
    if rec_id is None:
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    
    context.user_data[AWAITING_INPUT_KEY] = {
        "action": "close", 
        "rec_id": rec_id, 
        "original_message": query.message,
        "position_type": "rec"
    }
    
    await query.answer()
    await query.edit_message_text(
        f"{query.message.text}\n\n<b>✍️ الرجاء الرد على هذه الرسالة ↩️ بسعر الإغلاق المطلوب للتوصية #{rec_id}.</b>", 
        parse_mode=ParseMode.HTML
    )

@require_active_user
@require_analyst_user
@unit_of_work
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """إغلاق بسعر السوق"""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    
    if not rec_id:
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    
    await query.answer("جاري جلب سعر السوق والإغلاق...")
    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        await trade_service.close_recommendation_at_market_for_user_async(
            rec_id, str(query.from_user.id), db_session=db_session
        )
        await query.edit_message_text(f"✅ التوصية #{rec_id} تم إغلاقها بسعر السوق.")
    except Exception as e:
        log.error(f"فشل إغلاق التوصية #{rec_id} بسعر السوق: {e}", exc_info=True)
        await query.answer(f"خطأ: {e}", show_alert=True)

@require_active_user
@require_analyst_user
@unit_of_work
async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """تأكيد الإغلاق"""
    query = update.callback_query
    parts = parse_cq_parts(query.data)
    
    try:
        rec_id, exit_price = int(parts[2]), parse_number(parts[3])
    except (ValueError, IndexError) as e:
        await query.answer(f"قيمة غير صالحة: {e}", show_alert=True)
        return
    
    await query.answer("جاري إغلاق التوصية...")
    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        await trade_service.close_recommendation_for_user_async(
            rec_id, str(query.from_user.id), exit_price, reason="MANUAL_CLOSE", db_session=db_session
        )
        await query.edit_message_text(f"✅ التوصية #{rec_id} تم إغلاقها بنجاح.")
    except Exception as e:
        log.error(f"فشل إغلاق التوصية #{rec_id} عبر التأكيد: {e}", exc_info=True)
        await query.edit_message_text(f"❌ فشل إغلاق التوصية #{rec_id}. الخطأ: {e}")
    finally:
        context.user_data.pop(AWAITING_INPUT_KEY, None)

@require_active_user
@require_analyst_user
@unit_of_work
async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """إلغاء عملية الإغلاق"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    rec_id = parse_tail_int(query.data)
    
    if rec_id:
        await _send_or_edit_position_panel(
            context, db_session, 
            query.message.chat_id, query.message.message_id, 
            rec_id, query.from_user.id, 'rec'
        )

@require_active_user
@require_analyst_user
async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة التعديل"""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    
    if rec_id is None:
        return
    
    keyboard = analyst_edit_menu_keyboard(rec_id)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=keyboard)

@require_active_user
@require_analyst_user
async def start_edit_sl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء تعديل وقف الخسارة"""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    
    if rec_id is None:
        return
    
    context.user_data[AWAITING_INPUT_KEY] = {
        "action": "edit_sl", 
        "rec_id": rec_id, 
        "original_message": query.message,
        "position_type": "rec"
    }
    
    await query.answer()
    await query.edit_message_text(
        f"{query.message.text}\n\n<b>✏️ الرجاء الرد على هذه الرسالة ↩️ بقيمة وقف الخسارة الجديدة للتوصية #{rec_id}.</b>", 
        parse_mode=ParseMode.HTML
    )

@require_active_user
@require_analyst_user
async def start_edit_tp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء تعديل الأهداف"""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    
    if rec_id is None:
        return
    
    context.user_data[AWAITING_INPUT_KEY] = {
        "action": "edit_tp", 
        "rec_id": rec_id, 
        "original_message": query.message,
        "position_type": "rec"
    }
    
    await query.answer()
    await query.edit_message_text(
        f"{query.message.text}\n\n<b>🎯 الرجاء الرد على هذه الرسالة ↩️ بالأهداف الجديدة للتوصية #{rec_id} (مفصولة بمسافات).</b>", 
        parse_mode=ParseMode.HTML
    )

@require_active_user
@require_analyst_user
@unit_of_work
async def strategy_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """عرض قائمة استراتيجية الخروج"""
    query = update.callback_query
    await query.answer()
    
    rec_id = parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_strategy_menu(
            context, db_session, 
            query.message.chat_id, query.message.message_id, 
            rec_id, query.from_user.id
        )

# ==================== معالجات الصفقات الشخصية ====================

@unit_of_work
async def update_trade_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """تحديث سعر الصفقة الشخصية"""
    query = update.callback_query
    await query.answer("جاري تحديث السعر...")
    
    trade_id = parse_tail_int(query.data)
    if not trade_id:
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    
    # إعادة تحميل اللوحة لتحديث السعر الحي
    await _send_or_edit_position_panel(
        context, db_session, 
        query.message.chat_id, query.message.message_id, 
        trade_id, query.from_user.id, 'trade'
    )

@unit_of_work
async def show_trade_performance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """عرض أداء الصفقة الشخصية"""
    query = update.callback_query
    await query.answer("جاري تحميل تفاصيل الأداء...")
    
    trade_id = parse_tail_int(query.data)
    if not trade_id:
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    
    trade_service = get_service(context, "trade_service", TradeService)
    trade_details = trade_service.get_user_trade_details(db_session, trade_id, str(query.from_user.id))
    
    if not trade_details:
        await query.answer("الصفقة غير موجودة.", show_alert=True)
        return
    
    # بناء نص تفاصيل الأداء
    performance_text = (
        f"<b>📊 أداء الصفقة #{trade_id}</b>\n"
        f"────────────────────\n"
        f"<b>الأصل:</b> {trade_details['asset']}\n"
        f"<b>الاتجاه:</b> {trade_details['side']}\n"
        f"<b>سعر الدخول:</b> {trade_details['entry']:g}\n"
        f"<b>وقف الخسارة:</b> {trade_details['stop_loss']:g}\n"
        f"<b>الحالة:</b> {trade_details['status']}\n"
    )
    
    if trade_details['current_pnl'] is not None:
        performance_text += f"<b>الربح/الخسارة الحالي:</b> {trade_details['current_pnl']:+.2f}%\n"
    
    if trade_details['realized_pnl'] is not None:
        performance_text += f"<b>الربح/الخسارة المحقق:</b> {trade_details['realized_pnl']:+.2f}%\n"
    
    # إضافة الأهداف
    performance_text += f"\n<b>🎯 الأهداف:</b>\n"
    for i, target in enumerate(trade_details['targets'], 1):
        price = target.get('price', 0)
        close_percent = target.get('close_percent', 0)
        performance_text += f"  {i}. {price:g} ({close_percent}%)\n"
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ العودة", callback_data=f"pos:show_panel:trade:{trade_id}")
    ]])
    
    await query.edit_message_text(
        text=performance_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@unit_of_work
async def close_trade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """بدء عملية إغلاق الصفقة الشخصية"""
    query = update.callback_query
    trade_id = parse_tail_int(query.data)
    
    if trade_id is None:
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    
    context.user_data[AWAITING_INPUT_KEY] = {
        "action": "close_trade", 
        "trade_id": trade_id, 
        "original_message": query.message,
        "position_type": "trade"
    }
    
    await query.answer()
    await query.edit_message_text(
        f"{query.message.text}\n\n<b>✍️ الرجاء الرد على هذه الرسالة ↩️ بسعر الإغلاق المطلوب للصفقة #{trade_id}.</b>", 
        parse_mode=ParseMode.HTML
    )

# ==================== معالج الردود الموحد ====================

@unit_of_work
async def unified_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """معالج موحد لجميع الردود النصية"""
    if not update.message or not context.user_data:
        return
    
    state = context.user_data.get(AWAITING_INPUT_KEY)
    if not state:
        return
    
    original_message = state.get("original_message")
    if not original_message or not update.message.reply_to_message:
        return
    
    if update.message.reply_to_message.message_id != original_message.message_id:
        return
    
    action = state["action"]
    position_type = state.get("position_type", "rec")
    position_id = state.get("rec_id") or state.get("trade_id")
    user_input = update.message.text.strip()
    chat_id, message_id, user_id = original_message.chat_id, original_message.message_id, update.effective_user.id
    user_id_str = str(user_id)
    
    try:
        # حذف رسالة المستخدم
        try: 
            await update.message.delete()
        except Exception: 
            pass
        
        trade_service = get_service(context, "trade_service", TradeService)
        
        if action == "close" and position_type == "rec":
            # إغلاق توصية
            exit_price = parse_number(user_input)
            text = f"تأكيد إغلاق <b>#{position_id}</b> بسعر <b>{exit_price:g}</b>؟"
            keyboard = confirm_close_keyboard(position_id, exit_price)
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, 
                text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
            return

        elif action == "close_trade" and position_type == "trade":
            # إغلاق صفقة شخصية
            exit_price = parse_number(user_input)
            result = await trade_service.close_user_trade_async(
                position_id, user_id_str, exit_price, db_session=db_session
            )
            
            if result['success']:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id,
                    text=f"✅ الصفقة #{position_id} تم إغلاقها بنجاح. الربح/الخسارة: {result['pnl_percent']:+.2f}%"
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id,
                    text=f"❌ فشل إغلاق الصفقة: {result['error']}"
                )
            
            context.user_data.pop(AWAITING_INPUT_KEY, None)
            return

        elif action == "edit_sl" and position_type == "rec":
            # تعديل وقف الخسارة للتوصية
            new_sl = parse_number(user_input)
            await trade_service.update_sl_for_user_async(position_id, user_id_str, new_sl, db_session=db_session)
        
        elif action == "edit_tp" and position_type == "rec":
            # تعديل الأهداف للتوصية
            new_targets = parse_targets_list(user_input.split())
            await trade_service.update_targets_for_user_async(position_id, user_id_str, new_targets, db_session=db_session)

        # إذا نجحت العملية، تنظيف الحالة وعرض اللوحة الرئيسية
        context.user_data.pop(AWAITING_INPUT_KEY, None)
        await _send_or_edit_position_panel(
            context, db_session, chat_id, message_id, 
            position_id, user_id, position_type
        )

    except Exception as e:
        log.error(f"خطأ في معالجة الإدخال للإجراء {action}, المعرف {position_id}: {e}", exc_info=True)
        context.user_data.pop(AWAITING_INPUT_KEY, None)
        
        try: 
            await context.bot.send_message(chat_id=chat_id, text=f"❌ خطأ: {e}")
        except Exception: 
            pass
        
        # العودة دائماً إلى اللوحة الرئيسية عند الخطأ
        await _send_or_edit_position_panel(
            context, db_session, chat_id, message_id, 
            position_id, user_id, position_type
        )

# ==================== تسجيل المعالجات ====================

def register_management_handlers(application: Application):
    """تسجيل جميع معالجات استدعاء البوت"""
    
    # التنقل الرئيسي وعرض اللوحات
    application.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=r"^open_nav:page:", block=False))
    application.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^pos:show_panel:", block=False))
    application.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^rec:back_to_main:", block=False))
    application.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^trade:back:", block=False))
    
    # إلغاء التوصية المعلقة
    application.add_handler(CallbackQueryHandler(cancel_pending_rec_handler, pattern=r"^rec:cancel_pending:", block=False))

    # سير عمل الإغلاق الكامل
    application.add_handler(CallbackQueryHandler(show_close_menu_handler, pattern=r"^rec:close_menu:", block=False))
    application.add_handler(CallbackQueryHandler(close_at_market_handler, pattern=r"^rec:close_market:", block=False))
    application.add_handler(CallbackQueryHandler(close_with_manual_price_handler, pattern=r"^rec:close_manual:", block=False))
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:", block=False))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:", block=False))

    # سير عمل التعديل
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:", block=False))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:", block=False))
    application.add_handler(CallbackQueryHandler(start_edit_tp_handler, pattern=r"^rec:edit_tp:", block=False))
    
    # سير عمل الاستراتيجية
    application.add_handler(CallbackQueryHandler(strategy_menu_handler, pattern=r"^rec:strategy_menu:", block=False))

    # معالجات الصفقات الشخصية
    application.add_handler(CallbackQueryHandler(update_trade_price_handler, pattern=r"^trade:update:", block=False))
    application.add_handler(CallbackQueryHandler(show_trade_performance_handler, pattern=r"^trade:performance:", block=False))
    application.add_handler(CallbackQueryHandler(close_trade_handler, pattern=r"^trade:close:", block=False))
    application.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^trade:edit:", block=False))

    # معالج موحد لجميع الردود النصية في المحادثة
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler), group=1)

# تصدير المتغيرات المهمة
__all__ = [
    'register_management_handlers',
    'AWAITING_INPUT_KEY',
    'unified_reply_handler'
]