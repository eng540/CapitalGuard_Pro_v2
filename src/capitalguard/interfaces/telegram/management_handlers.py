# src/capitalguard/interfaces/telegram/management_handlers.py (v28.8 - Production Ready)
"""
إصدار إنتاجي محسَن لإدارة التوصيات والصفقات
✅ إصلاح جميع مشاكل التجميد وعدم الاستجابة
✅ تحسين نظام المهلات ومعالجة الأخطاء
✅ دعم كامل للواجهات التفاعلية
"""

import logging
import time
from decimal import Decimal, InvalidOperation

from telegram import Update, ReplyKeyboardRemove, CallbackQuery
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, 
    ContextTypes, filters, ConversationHandler, CommandHandler
)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_cq_parts
from .keyboards import (
    analyst_control_panel_keyboard, build_open_recs_keyboard, 
    build_user_trade_control_keyboard, build_close_options_keyboard, 
    analyst_edit_menu_keyboard, build_exit_strategy_keyboard, 
    build_partial_close_keyboard, CallbackAction, CallbackNamespace
)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- Conversation States ---
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)
AWAITING_INPUT_KEY = "awaiting_user_input_for"
LAST_ACTIVITY_KEY = "last_activity_management"

# --- Timeout Configuration ---
MANAGEMENT_TIMEOUT = 1800  # 30 دقيقة

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف حالة الإدارة"""
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(LAST_ACTIVITY_KEY, None)
    context.user_data.pop('partial_close_rec_id', None)
    context.user_data.pop('partial_close_percent', None)

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    """تحديث وقت النشاط الأخير للإدارة"""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def check_management_timeout(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """التحقق من انتهاء مدة جلسة الإدارة"""
    last_activity = context.user_data.get(LAST_ACTIVITY_KEY, 0)
    current_time = time.time()
    return current_time - last_activity > MANAGEMENT_TIMEOUT

async def safe_edit_message(query: CallbackQuery, text: str = None, reply_markup=None, parse_mode: str = None) -> bool:
    """تحرير الرسالة بشكل آمن مع استعادة الأخطاء"""
    try:
        if text is not None:
            await query.edit_message_text(
                text=text, 
                reply_markup=reply_markup, 
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
        elif reply_markup is not None:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return True
        loge.warning(f"Handled BadRequest in safe_edit_message: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError in safe_edit_message: {e}")
        return False

async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة انتهاء مدة جلسة الإدارة"""
    if check_management_timeout(context):
        clean_management_state(context)
        if update.callback_query:
            await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
            await safe_edit_message(update.callback_query, "⏰ انتهت مدة الجلسة. يرجى البدء من جديد.")
        return True
    return False

# --- Core Panel Rendering ---

async def _send_or_edit_position_panel(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int):
    """إرسال أو تعديل لوحة المركز بشكل آمن"""
    try:
        user_id = str(query.from_user.id)
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(db_session, user_id, position_type, position_id)
        
        if not position:
            await safe_edit_message(query, text="❌ المركز غير موجود أو غير مسموح بالوصول.")
            return

        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
        if live_price: 
            setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)
        is_trade = getattr(position, 'is_user_trade', False)
        
        keyboard = None
        if is_trade:
            keyboard = build_user_trade_control_keyboard(position_id)
        elif position.status != RecommendationStatus.CLOSED:
            keyboard = analyst_control_panel_keyboard(position)
        
        await safe_edit_message(query, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        loge.error(f"Error in position panel: {e}")
        await safe_edit_message(query, text=f"❌ خطأ في تحميل البيانات: {str(e)}")

# --- Handlers ---

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة عرض لوحة المركز"""
    query = update.callback_query
    await query.answer()
    
    if await handle_management_timeout(update, context):
        return
        
    update_management_activity(context)
    parts = parse_cq_parts(query.data)
    
    try:
        if parts[1] == "back_to_main":
            position_id = int(parts[2])
            position_type = 'rec'
        else:
            position_type, position_id = parts[2], int(parts[3])
        
        await _send_or_edit_position_panel(query, context, db_session, position_type, position_id)
    except (IndexError, ValueError) as e:
        loge.error(f"Could not parse position info from callback data: {query.data}, error: {e}")
        await query.answer("❌ خطأ في بيانات الاستدعاء.", show_alert=True)
        await safe_edit_message(query, text="❌ بيانات غير صالحة. يرجى المحاولة مرة أخرى.")

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة التنقل بين المراكز المفتوحة"""
    query = update.callback_query
    await query.answer()
    
    if await handle_management_timeout(update, context):
        return
        
    update_management_activity(context)
    parts = parse_cq_parts(query.data)
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(query.from_user.id))
        
        if not items:
            await safe_edit_message(query, text="✅ لا توجد مراكز مفتوحة.")
            return
            
        keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        await safe_edit_message(
            query, 
            text="<b>📊 المراكز المفتوحة</b>\nاختر مركزاً للإدارة:", 
            reply_markup=keyboard, 
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        loge.error(f"Error in open positions navigation: {e}")
        await safe_edit_message(query, text="❌ خطأ في تحميل المراكز المفتوحة.")

@uow_transaction
@require_active_user
@require_analyst_user
async def show_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة عرض القوائم المختلفة"""
    query = update.callback_query
    await query.answer()
    
    if await handle_management_timeout(update, context):
        return
        
    update_management_activity(context)
    parts = parse_cq_parts(query.data)
    action, rec_id = parts[1], int(parts[2])

    try:
        keyboard = None
        if action == "edit_menu":
            keyboard = analyst_edit_menu_keyboard(rec_id)
        elif action == "close_menu":
            keyboard = build_close_options_keyboard(rec_id)
        elif action == "strategy_menu":
            trade_service = get_service(context, "trade_service", TradeService)
            rec = trade_service.repo.get(db_session, rec_id)
            if rec:
                rec_entity = trade_service.repo._to_entity(rec)
                keyboard = build_exit_strategy_keyboard(rec_entity)
        elif action == CallbackAction.PARTIAL.value:
            keyboard = build_partial_close_keyboard(rec_id)
        
        if keyboard:
            await safe_edit_message(query, reply_markup=keyboard)
        else:
            await query.answer("❌ تعذر تحميل القائمة.", show_alert=True)
            
    except Exception as e:
        loge.error(f"Error in menu handler: {e}")
        await query.answer("❌ خطأ في تحميل القائمة.", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def set_strategy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة تعيين استراتيجية الخروج"""
    query = update.callback_query
    await query.answer("جاري تحديث الاستراتيجية...")
    
    if await handle_management_timeout(update, context):
        return
        
    update_management_activity(context)
    parts = parse_cq_parts(query.data)
    rec_id, strategy_value = int(parts[2]), parts[3]
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.update_exit_strategy_async(rec_id, str(query.from_user.id), ExitStrategy(strategy_value), db_session)
        
        await _send_or_edit_position_panel(query, context, db_session, position_type='rec', position_id=rec_id)
        
    except Exception as e:
        loge.error(f"Error setting strategy: {e}")
        await query.answer(f"❌ فشل تحديث الاستراتيجية: {str(e)}", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة الإغلاق بسعر السوق"""
    query = update.callback_query
    await query.answer("جاري جلب سعر السوق والإغلاق...")
    
    if await handle_management_timeout(update, context):
        return
        
    update_management_activity(context)
    rec_id = int(parse_cq_parts(query.data)[2])
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        
        rec_orm = trade_service.repo.get(db_session, rec_id)
        if not rec_orm: 
            await query.answer("❌ التوصية غير موجودة.", show_alert=True)
            return
        
        live_price = await price_service.get_cached_price(rec_orm.asset, rec_orm.market, force_refresh=True)
        if live_price is None:
            await query.answer(f"❌ تعذر جلب سعر السوق لـ {rec_orm.asset}.", show_alert=True)
            return
            
        await trade_service.close_recommendation_async(rec_id, str(query.from_user.id), Decimal(str(live_price)), db_session)
        await _send_or_edit_position_panel(query, context, db_session, position_type='rec', position_id=rec_id)
        
    except Exception as e:
        loge.error(f"Error in market close: {e}")
        await query.answer(f"❌ فشل الإغلاق: {str(e)}", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة الإغلاق الجزئي بنسبة ثابتة"""
    query = update.callback_query
    await query.answer("جاري جلب السعر وإجراء الإغلاق الجزئي...")
    
    if await handle_management_timeout(update, context):
        return
        
    update_management_activity(context)
    parts = parse_cq_parts(query.data)
    rec_id, percent_to_close = int(parts[2]), Decimal(parts[3])
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        
        rec_orm = trade_service.repo.get(db_session, rec_id)
        if not rec_orm: 
            await query.answer("❌ التوصية غير موجودة.", show_alert=True)
            return
        
        live_price = await price_service.get_cached_price(rec_orm.asset, rec_orm.market, force_refresh=True)
        if live_price is None:
            await query.answer(f"❌ تعذر جلب سعر السوق لـ {rec_orm.asset}.", show_alert=True)
            return
            
        await trade_service.partial_close_async(rec_id, str(query.from_user.id), percent_to_close, Decimal(str(live_price)), db_session)
        await _send_or_edit_position_panel(query, context, db_session, position_type='rec', position_id=rec_id)
        
    except Exception as e:
        loge.error(f"Error in partial close: {e}")
        await query.answer(f"❌ فشل الإغلاق الجزئي: {str(e)}", show_alert=True)

async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة طلب الإدخال من المستخدم"""
    query = update.callback_query
    await query.answer()
    
    if await handle_management_timeout(update, context):
        return
        
    update_management_activity(context)
    parts = parse_cq_parts(query.data)
    action, rec_id = parts[1], int(parts[2])
    
    prompts = {
        "edit_sl": "✏️ الرجاء إرسال وقف الخسارة الجديد:",
        "edit_tp": "🎯 الرجاء إرسال قائمة الأهداف الجديدة (مثال: 50000 52000@50):",
        "close_manual": "✍️ الرجاء إرسال سعر الإغلاق النهائي:"
    }
    
    context.user_data[AWAITING_INPUT_KEY] = {
        "action": action, 
        "rec_id": rec_id, 
        "original_query": query,
        "original_text": query.message.text,
        "original_reply_markup": query.message.reply_markup
    }
    
    full_prompt = f"{query.message.text}\n\n<b>{prompts.get(action, 'الرجاء إرسال القيمة الجديدة:')}</b>"
    await safe_edit_message(query, text=full_prompt, parse_mode=ParseMode.HTML)

@uow_transaction
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة ردود المستخدم على الطلبات"""
    if await handle_management_timeout(update, context):
        return
        
    update_management_activity(context)
    
    state = context.user_data.pop(AWAITING_INPUT_KEY, None)
    if not state:
        return
    
    original_query = state.get("original_query")
    if not original_query or not update.message.reply_to_message:
        context.user_data[AWAITING_INPUT_KEY] = state
        return

    action, rec_id = state["action"], state["rec_id"]
    user_input = update.message.text.strip()
    
    try: 
        await update.message.delete()
    except Exception: 
        pass

    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        if action == "close_manual":
            price = parse_number(user_input)
            if price is None: 
                raise ValueError("تنسيق السعر غير صالح.")
            await trade_service.close_recommendation_async(rec_id, str(update.effective_user.id), price, db_session)
            
        elif action == "edit_sl":
            price = parse_number(user_input)
            if price is None: 
                raise ValueError("تنسيق السعر غير صالح.")
            await trade_service.update_sl_for_user_async(rec_id, str(update.effective_user.id), price, db_session)
            
        elif action == "edit_tp":
            targets_list = parse_targets_list(user_input.split())
            if not targets_list: 
                raise ValueError("تنسيق الأهداف غير صالح.")
            await trade_service.update_targets_for_user_async(rec_id, str(update.effective_user.id), targets_list, db_session)
        
        await _send_or_edit_position_panel(original_query, context, db_session, 'rec', rec_id)

    except Exception as e:
        loge.error(f"Error processing reply for {action} on #{rec_id}: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=f"❌ خطأ: {e}\n\nيرجى المحاولة مرة أخرى."
        )
        context.user_data[AWAITING_INPUT_KEY] = state
        
        # استعادة الرسالة الأصلية
        await safe_edit_message(
            original_query, 
            text=state.get("original_text", "حدث خطأ، يرجى المحاولة مرة أخرى"),
            reply_markup=state.get("original_reply_markup")
        )

async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بدء عملية الإغلاق الجزئي المخصص"""
    query = update.callback_query
    await query.answer()
    
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
        
    update_management_activity(context)
    rec_id = int(parse_cq_parts(query.data)[2])
    context.user_data['partial_close_rec_id'] = rec_id
    
    await safe_edit_message(
        query, 
        text=f"{query.message.text}\n\n<b>💰 الرجاء إرسال النسبة المئوية للإغلاق (مثال: 25.5)</b>", 
        parse_mode=ParseMode.HTML
    )
    return AWAIT_PARTIAL_PERCENT

async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة استلام نسبة الإغلاق الجزئي"""
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
        
    update_management_activity(context)
    
    try:
        percent = parse_number(update.message.text)
        if not (percent and 0 < percent <= 100):
            raise ValueError("يجب أن تكون النسبة رقم بين 0 و 100.")
        
        context.user_data['partial_close_percent'] = percent
        await update.message.reply_html(f"✅ النسبة: {percent:g}%\n\n<b>الآن، الرجاء إرسال سعر الإغلاق.</b>")
        return AWAIT_PARTIAL_PRICE
        
    except ValueError as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. يرجى المحاولة مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PERCENT
    except Exception as e:
        loge.error(f"Error in partial close percent: {e}")
        await update.message.reply_text("❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """معالجة استلام سعر الإغلاق الجزئي"""
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
        
    update_management_activity(context)
    
    try:
        price = parse_number(update.message.text)
        if price is None: 
            raise ValueError("تنسيق السعر غير صالح.")
        
        percent = context.user_data.pop('partial_close_percent')
        rec_id = context.user_data.pop('partial_close_rec_id')
        
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.partial_close_async(rec_id, str(update.effective_user.id), percent, price, db_session)
        await update.message.reply_text("✅ تم الإغلاق الجزئي بنجاح.")
        
    except (ValueError, KeyError) as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة أو انتهت الجلسة: {e}. يرجى المحاولة مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PRICE
    except Exception as e:
        loge.error(f"Error in partial close flow for rec #{context.user_data.get('partial_close_rec_id')}: {e}")
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")
    
    clean_management_state(context)
    return ConversationHandler.END

async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء عملية الإغلاق الجزئي"""
    clean_management_state(context)
    await update.message.reply_text(
        "❌ تم إلغاء عملية الإغلاق الجزئي.", 
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def register_management_handlers(app: Application):
    """تسجيل معالجات الإدارة"""
    ns_rec = CallbackNamespace.RECOMMENDATION.value
    ns_nav = CallbackNamespace.NAVIGATION.value
    ns_pos = CallbackNamespace.POSITION.value
    
    act_nv = CallbackAction.NAVIGATE.value
    act_sh = CallbackAction.SHOW.value
    act_st = CallbackAction.STRATEGY.value
    act_pt = CallbackAction.PARTIAL.value

    # معالجات التنقل والعرض
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{ns_nav}:{act_nv}:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^(?:{ns_pos}:{act_sh}:|{ns_rec}:back_to_main:)"))
    
    # معالجات القوائم
    app.add_handler(CallbackQueryHandler(show_menu_handler, pattern=rf"^{ns_rec}:(?:edit_menu|close_menu|strategy_menu|{act_pt})"))
    
    # معالجات الإجراءات
    app.add_handler(CallbackQueryHandler(set_strategy_handler, pattern=rf"^{ns_rec}:{act_st}:"))
    app.add_handler(CallbackQueryHandler(close_at_market_handler, pattern=rf"^{ns_rec}:close_market:"))
    app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{ns_rec}:{act_pt}:"))
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^{ns_rec}:(?:edit_sl|edit_tp|close_manual)"))
    
    # معالج الردود
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, reply_handler))

    # محادثة الإغلاق الجزئي المخصص
    partial_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_close_custom_start, pattern=rf"^{ns_rec}:partial_close_custom:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_percent_received)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_price_received)],
        },
        fallbacks=[CommandHandler("cancel", partial_close_cancel)],
        name="partial_profit_conversation",
        per_user=True, 
        per_chat=True, 
        per_message=False,
        conversation_timeout=MANAGEMENT_TIMEOUT,
    )
    app.add_handler(partial_close_conv)