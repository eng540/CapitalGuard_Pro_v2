# src/capitalguard/interfaces/telegram/management_handlers.py (v29.0 - FINAL COMPLETE)
"""
الإصدار النهائي الكامل لمعالجات إدارة التوصيات والصفحات
✅ نظام callback متكامل
✅ محادثات تفاعلية كاملة
✅ معالجة أخطاء محسنة
✅ دعم كامل للغة العربية
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from telegram import Update, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, ContextTypes, 
    filters, ConversationHandler, CommandHandler
)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_tail_int, parse_cq_parts
from .keyboards import (
    analyst_control_panel_keyboard,
    build_open_recs_keyboard, 
    build_user_trade_control_keyboard,
    build_close_options_keyboard,
    analyst_edit_menu_keyboard,
    build_exit_strategy_keyboard,
    build_partial_close_keyboard,
    CallbackBuilder,
    CallbackNamespace,
    CallbackAction
)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

# حالات المحادثة
AWAITING_INPUT_KEY = "awaiting_user_input_for"
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)

# --- الدوال المساعدة ---

async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """إرسال أو تعديل لوحة التحكم بالمركز"""
    query = update.callback_query
    parts = parse_cq_parts(query.data)
    
    try:
        if parts[1] == "back_to_main":
            position_id = int(parts[2])
            position_type = 'rec'
        else:
            position_type, position_id = parts[2], int(parts[3])
    except (IndexError, ValueError):
        log.error(f"Could not parse position info from callback data: {query.data}")
        await query.answer("❌ خطأ في بيانات الاستدعاء.", show_alert=True)
        return

    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(
        db_session, str(query.from_user.id), position_type, position_id
    )
    
    if not position:
        await query.edit_message_text("❌ المركز غير موجود أو الوصول مرفوض.")
        return

    # الحصول على السعر الحي
    price_service = get_service(context, "price_service", PriceService)
    live_price = await price_service.get_cached_price(
        position.asset.value, position.market, force_refresh=True
    )
    if live_price: 
        setattr(position, "live_price", live_price)

    # بناء النص ولوحة المفاتيح
    text = build_trade_card_text(position)
    is_trade = getattr(position, 'is_user_trade', False)
    
    keyboard = None
    if is_trade:
        keyboard = build_user_trade_control_keyboard(position_id)
    elif position.status != RecommendationStatus.CLOSED:
        keyboard = analyst_control_panel_keyboard(position)

    try:
        await query.edit_message_text(
            text, 
            reply_markup=keyboard, 
            parse_mode=ParseMode.HTML, 
            disable_web_page_preview=True
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            await query.answer()
        else:
            log.warning(f"Failed to edit panel message: {e}")

async def _prompt_for_input(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, action: str, prompt_text: str):
    """طلب إدخال من المستخدم"""
    rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_INPUT_KEY] = {
        "action": action, 
        "rec_id": rec_id, 
        "original_message": query.message
    }
    full_prompt = f"{query.message.text}\n\n<b>{prompt_text}</b>"
    await query.edit_message_text(full_prompt, parse_mode=ParseMode.HTML)

# --- المعالجات الرئيسية والتنقل ---

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """عرض لوحة التحكم بالمركز"""
    await update.callback_query.answer()
    await _send_or_edit_position_panel(update, context, db_session)

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """التنقل بين المراكز المفتوحة"""
    query = update.callback_query
    await query.answer()
    page = parse_tail_int(query.data) or 1
    
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    
    items = trade_service.get_open_positions_for_user(db_session, str(query.from_user.id))
    
    if not items:
        await query.edit_message_text(text="✅ لا توجد لديك مراكز مفتوحة.")
        return
        
    keyboard = await build_open_recs_keyboard(
        items, 
        current_page=page, 
        price_service=price_service
    )
    
    await query.edit_message_text(
        text="<b>📊 مراكزك المفتوحة</b>\nاختر مركزاً للإدارة:",
        reply_markup=keyboard, 
        parse_mode=ParseMode.HTML
    )

async def open_positions_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر عرض المراكز المفتوحة"""
    try:
        with uow_transaction() as db_session:
            trade_service = get_service(context, "trade_service", TradeService)
            price_service = get_service(context, "price_service", PriceService)
            
            user_telegram_id = str(update.effective_user.id)
            items = trade_service.get_open_positions_for_user(db_session, user_telegram_id)
            
            if not items:
                await update.message.reply_text("✅ لا توجد لديك مراكز مفتوحة.")
                return
            
            keyboard = await build_open_recs_keyboard(items, 1, price_service)
            
            await update.message.reply_text(
                "<b>📊 مراكزك المفتوحة</b>\nاختر مركزاً للإدارة:",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        log.error(f"Error in open_positions_command_handler: {e}", exc_info=True)
        await update.message.reply_text("❌ حدث خطأ أثناء تحميل المراكز المفتوحة.")

# --- معالجات القوائم ---

@uow_transaction
@require_active_user
@require_analyst_user
async def show_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """عرض القوائم المختلفة"""
    query = update.callback_query
    await query.answer()
    parts = parse_cq_parts(query.data)
    action, rec_id = parts[1], int(parts[2])

    if action == "edit_menu":
        await query.edit_message_reply_markup(reply_markup=analyst_edit_menu_keyboard(rec_id))
    elif action == "close_menu":
        await query.edit_message_reply_markup(reply_markup=build_close_options_keyboard(rec_id))
    elif action == "strategy_menu":
        trade_service = get_service(context, "trade_service", TradeService)
        rec_orm = trade_service.repo.get(db_session, rec_id)
        if rec_orm:
            rec_entity = trade_service.repo._to_entity(rec_orm)
            await query.edit_message_reply_markup(reply_markup=build_exit_strategy_keyboard(rec_entity))
    elif action == "close_partial":
        await query.edit_message_reply_markup(reply_markup=build_partial_close_keyboard(rec_id))

# --- الإجراءات المباشرة ---

@uow_transaction
@require_active_user
@require_analyst_user
async def set_strategy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """تعيين استراتيجية الخروج"""
    query = update.callback_query
    await query.answer("🔄 جاري تحديث الاستراتيجية...")
    
    parts = parse_cq_parts(query.data)
    rec_id, strategy_value = int(parts[2]), parts[3]
    
    trade_service = get_service(context, "trade_service", TradeService)
    await trade_service.update_exit_strategy_async(
        rec_id, str(query.from_user.id), ExitStrategy(strategy_value), db_session
    )

@uow_transaction
@require_active_user
@require_analyst_user
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """إغلاق بالسعر السوقي"""
    query = update.callback_query
    await query.answer("🔄 جاري الحصول على السعر السوقي والإغلاق...")
    
    rec_id = int(query.data.split(':')[2])
    trade_service = get_service(context, "trade_service", TradeService)
    
    rec_orm = trade_service.repo.get(db_session, rec_id)
    if not rec_orm: 
        return
        
    rec_entity = trade_service.repo._to_entity(rec_orm)
    
    price_service = get_service(context, "price_service", PriceService)
    live_price = await price_service.get_cached_price(
        rec_entity.asset.value, rec_entity.market, force_refresh=True
    )
    
    if live_price is None:
        await query.answer(
            f"❌ تعذر الحصول على السعر السوقي لـ {rec_entity.asset.value}.",
            show_alert=True
        )
        return
        
    await trade_service.close_recommendation_async(
        rec_id, str(query.from_user.id), Decimal(str(live_price)), db_session
    )

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """إغلاق جزئي بنسبة ثابتة"""
    query = update.callback_query
    await query.answer("🔄 جاري الحصول على السعر وإغلاق جزء من المركز...")
    
    parts = parse_cq_parts(query.data)
    rec_id, percent_to_close = int(parts[2]), Decimal(parts[3])
    
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    
    rec_orm = trade_service.repo.get(db_session, rec_id)
    if not rec_orm: 
        return
        
    rec_entity = trade_service.repo._to_entity(rec_orm)
    
    live_price = await price_service.get_cached_price(
        rec_entity.asset.value, rec_entity.market, force_refresh=True
    )
    
    if live_price is None:
        await query.answer(
            f"❌ تعذر الحصول على السعر السوقي لـ {rec_entity.asset.value}.",
            show_alert=True
        )
        return
        
    await trade_service.partial_close_async(
        rec_id, str(query.from_user.id), percent_to_close, Decimal(str(live_price)), db_session
    )

# --- معالجات الإدخال ---

async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب إدخال من المستخدم"""
    query = update.callback_query
    await query.answer()
    action = query.data.split(':')[1]
    
    prompts = {
        "edit_sl": "✏️ قم بالرد على هذه الرسالة بإدخال وقف الخسارة الجديد.",
        "edit_tp": "🎯 قم بالرد بإدخال الأهداف الجديدة (مثال: 50000 52000@50).",
        "close_manual": "✍️ قم بالرد بإدخال سعر الإغلاق النهائي."
    }
    
    await _prompt_for_input(
        query, context, action, prompts.get(action, "يرجى الرد بالقيمة الجديدة.")
    )

@uow_transaction
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة ردود المستخدم"""
    if not context.user_data or not (state := context.user_data.pop(AWAITING_INPUT_KEY, None)):
        return
        
    orig_msg = state.get("original_message")
    if not orig_msg or not update.message.reply_to_message or update.message.reply_to_message.message_id != orig_msg.message_id:
        if state: 
            context.user_data[AWAITING_INPUT_KEY] = state
        return

    action, rec_id = state["action"], state["rec_id"]
    user_input, chat_id, user_id = update.message.text.strip(), orig_msg.chat_id, str(update.effective_user.id)
    
    # حذف رسالة المستخدم
    try: 
        await update.message.delete()
    except Exception: 
        pass

    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        if action == "close_manual":
            price = parse_number(user_input)
            if price is None: 
                raise ValueError("تنسيق السعر غير صحيح.")
            await trade_service.close_recommendation_async(rec_id, user_id, price, db_session=db_session)
            
        elif action == "edit_sl":
            price = parse_number(user_input)
            if price is None: 
                raise ValueError("تنسيق السعر غير صحيح.")
            await trade_service.update_sl_for_user_async(rec_id, user_id, price, db_session=db_session)
            
        elif action == "edit_tp":
            targets_list = parse_targets_list(user_input.split())
            if not targets_list: 
                raise ValueError("تنسيق الأهداف غير صحيح.")
            await trade_service.update_targets_for_user_async(rec_id, user_id, targets_list, db_session=db_session)
            
    except Exception as e:
        log.error(f"Error processing reply for {action} on #{rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ خطأ: {e}")

# --- محادثة الإغلاق الجزئي ---

async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بدء محادثة الإغلاق الجزئي المخصص"""
    query = update.callback_query
    await query.answer()
    
    rec_id = int(query.data.split(':')[2])
    context.user_data['partial_close_rec_id'] = rec_id
    
    await query.edit_message_text(
        f"{query.message.text}\n\n<b>💰 يرجى إرسال النسبة المئوية للمركز التي تريد إغلاقها (مثال: 25.5).</b>",
        parse_mode=ParseMode.HTML
    )
    return AWAIT_PARTIAL_PERCENT

async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """استقبال النسبة المئوية للإغلاق الجزئي"""
    try:
        percent = parse_number(update.message.text)
        if percent is None or not (0 < percent <= 100):
            raise ValueError("يجب أن تكون النسبة المئوية بين 0 و 100.")
            
        context.user_data['partial_close_percent'] = percent
        
        await update.message.reply_html(
            f"✅ النسبة المئوية: {percent:g}%\n\n"
            f"<b>الآن، يرجى إرسال السعر الذي تريد الإغلاق عنده.</b>"
        )
        return AWAIT_PARTIAL_PRICE
        
    except ValueError as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. يرجى المحاولة مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """استقبال سعر الإغلاق الجزئي"""
    try:
        price = parse_number(update.message.text)
        if price is None: 
            raise ValueError("تنسيق السعر غير صحيح.")
        
        percent = context.user_data['partial_close_percent']
        rec_id = context.user_data['partial_close_rec_id']
        user_id = str(update.effective_user.id)
        
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.partial_close_async(rec_id, user_id, percent, price, db_session)
        
        await update.message.reply_text("✅ تم الإغلاق الجزئي بنجاح!")
        
    except ValueError as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. يرجى المحاولة مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PRICE
        
    except Exception as e:
        log.error(
            f"Error in partial profit flow for rec #{context.user_data.get('partial_close_rec_id')}: {e}", 
            exc_info=True
        )
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")
    
    # تنظيف بيانات المستخدم
    for key in ('partial_close_rec_id', 'partial_close_percent'):
        context.user_data.pop(key, None)
        
    return ConversationHandler.END

async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء محادثة الإغلاق الجزئي"""
    for key in ('partial_close_rec_id', 'partial_close_percent'):
        context.user_data.pop(key, None)
        
    await update.message.reply_text(
        "تم إلغاء عملية الإغلاق الجزئي.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# --- التسجيل ---

def register_management_handlers(app: Application):
    """تسجيل جميع معالجات الإدارة"""
    
    # معالجات التنقل والعرض
    app.add_handler(CallbackQueryHandler(
        navigate_open_positions_handler, 
        pattern=r"^open_nav:page:"
    ))
    
    app.add_handler(CallbackQueryHandler(
        show_position_panel_handler, 
        pattern=r"^(pos:show_panel:|rec:back_to_main:)"
    ))
    
    # معالجات القوائم
    app.add_handler(CallbackQueryHandler(
        show_menu_handler, 
        pattern=r"^rec:(edit_menu|close_menu|strategy_menu|close_partial)"
    ))
    
    # معالجات الإجراءات المباشرة
    app.add_handler(CallbackQueryHandler(
        set_strategy_handler, 
        pattern=r"^rec:set_strategy:"
    ))
    
    app.add_handler(CallbackQueryHandler(
        close_at_market_handler, 
        pattern=r"^rec:close_market:"
    ))
    
    app.add_handler(CallbackQueryHandler(
        partial_close_fixed_handler, 
        pattern=r"^rec:partial_close:"
    ))
    
    # معالجات الإدخال
    app.add_handler(CallbackQueryHandler(
        prompt_handler, 
        pattern=r"^rec:(edit_sl|edit_tp|close_manual)"
    ))
    
    app.add_handler(MessageHandler(
        filters.REPLY & filters.TEXT & ~filters.COMMAND, 
        reply_handler
    ))
    
    # محادثة الإغلاق الجزئي المخصص
    partial_close_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                partial_close_custom_start, 
                pattern=r"^rec:partial_close_custom:"
            )
        ],
        states={
            AWAIT_PARTIAL_PERCENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_percent_received)
            ],
            AWAIT_PARTIAL_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_price_received)
            ],
        },
        fallbacks=[CommandHandler("cancel", partial_close_cancel)],
        name="partial_profit_conversation",
        per_user=True,
        per_chat=True,
    )
    app.add_handler(partial_close_conv)
    
    # الأوامر النصية
    app.add_handler(CommandHandler(
        ["myportfolio", "open", "مراكزي"], 
        open_positions_command_handler
    ))

    log.info("✅ تم تحميل معالجات الإدارة بنجاح - الإصدار النهائي الكامل v29.0")