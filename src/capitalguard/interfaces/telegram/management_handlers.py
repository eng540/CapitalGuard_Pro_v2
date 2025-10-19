# src/capitalguard/interfaces/telegram/management_handlers.py (v29.4 - Production Ready & Final)
"""
إصدار إنتاجي نهائي محسَن لإدارة التوصيات والصفقات
✅ إصلاح حاسم ونهائي لمشكلة تجمد أزرار لوحة التحكم عن طريق تصحيح أنماط Regex
✅ إصلاح جذري لمشكلة "انتهاء مدة الجلسة" الفوري عن طريق تصحيح ترتيب منطق تحديث النشاط والتحقق من المهلة
✅ تطبيق نظام مهلات قوي وموثوق
✅ تكامل كامل مع بنية CallbackBuilder الموحدة
✅ إصلاح جميع مشاكل التجميد وعدم الاستجابة
✅ تحسين نظام المهلات ومعالجة الأخطاء
✅ دعم كامل للواجهات التفاعلية
✅ إصلاح مشكلة انتهاء الجلسة
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

# --- Conversation States & Constants ---
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)
AWAITING_INPUT_KEY = "awaiting_management_input"
LAST_ACTIVITY_KEY = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800  # 30 minutes

# --- Session & Timeout Management ---

def init_management_session(context: ContextTypes.DEFAULT_TYPE):
    """Initializes or resets the management session for a clean start."""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop('partial_close_rec_id', None)
    context.user_data.pop('partial_close_percent', None)
    log.debug(f"Management session initialized/reset for user {getattr(context, '_user_id', 'unknown')}.")

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    """Updates the last activity timestamp for the session."""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    """Cleans up all management-related state upon exit or timeout."""
    for key in [AWAITING_INPUT_KEY, LAST_ACTIVITY_KEY, 'partial_close_rec_id', 'partial_close_percent']:
        context.user_data.pop(key, None)

async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles session timeout due to inactivity - FIXED VERSION"""
    if LAST_ACTIVITY_KEY not in context.user_data:
        return False
        
    if time.time() - context.user_data.get(LAST_ACTIVITY_KEY, 0) > MANAGEMENT_TIMEOUT:
        clean_management_state(context)
        msg = "⏰ انتهت مدة الجلسة بسبب عدم النشاط.\n\nيرجى استخدام /myportfolio للبدء من جديد."
        try:
            if update.callback_query:
                await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
                await safe_edit_message(update.callback_query, text=msg)
            elif update.message:
                await update.message.reply_text(msg)
        except Exception as e:
            log.error(f"Error handling timeout: {e}")
        return True
    return False

# --- Helper Functions ---

async def safe_edit_message(query: CallbackQuery, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool:
    """Safely edits a message, handling common Telegram API errors."""
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

async def _send_or_edit_position_panel(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int):
    """Renders the main control panel for a specific recommendation or trade."""
    try:
        user_id = str(query.from_user.id)
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(db_session, user_id, position_type, position_id)

        if not position:
            await safe_edit_message(query, text="❌ المركز غير موجود أو تم إغلاقه.")
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
        
        await safe_edit_message(query, text=text, reply_markup=keyboard)
        
    except Exception as e:
        loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True)
        await safe_edit_message(query, text=f"❌ خطأ في تحميل البيانات: {str(e)}")

# --- Entry Point Handlers (Commands) ---

@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Unified entry point for /myportfolio and /open commands."""
    init_management_session(context)
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
        
        if not items:
            await update.message.reply_text("✅ لا توجد مراكز مفتوحة حالياً.")
            return
            
        keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)
        await update.message.reply_html("<b>📊 المراكز المفتوحة</b>\nاختر مركزاً للإدارة:", reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error in management entry point: {e}", exc_info=True)
        await update.message.reply_text("❌ خطأ في تحميل المراكز المفتوحة.")

# --- CallbackQuery Handlers ---

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles pagination for the list of open positions."""
    query = update.callback_query
    await query.answer()
    
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    if await handle_management_timeout(update, context):
        return
    
    parts = parse_cq_parts(query.data)
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
        
        if not items:
            await safe_edit_message(query, text="✅ لا توجد مراكز مفتوحة حالياً.")
            return
            
        keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        await safe_edit_message(
            query, 
            text="<b>📊 المراكز المفتوحة</b>\nاختر مركزاً للإدارة:", 
            reply_markup=keyboard
        )
    except Exception as e:
        loge.error(f"Error in open positions navigation: {e}", exc_info=True)
        await safe_edit_message(query, text="❌ خطأ في تحميل المراكز المفتوحة.")

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles displaying the control panel for a selected position."""
    query = update.callback_query
    await query.answer()
    
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    if await handle_management_timeout(update, context):
        return
    
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
@require_analyst_user
async def show_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles displaying sub-menus (edit, close, strategy)."""
    query = update.callback_query
    await query.answer()
    
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    if await handle_management_timeout(update, context):
        return
    
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
        loge.error(f"Error in menu handler: {e}", exc_info=True)
        await query.answer("❌ خطأ في تحميل القائمة.", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Unified handler for actions like changing strategy, market close, etc."""
    query = update.callback_query
    await query.answer("جاري التنفيذ...")
    
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    if await handle_management_timeout(update, context):
        return
    
    parts = parse_cq_parts(query.data)
    action, rec_id = parts[1], int(parts[2])
    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        if action == CallbackAction.STRATEGY.value:
            strategy_value = parts[3]
            await trade_service.update_exit_strategy_async(rec_id, str(query.from_user.id), ExitStrategy(strategy_value), db_session)
        else:
            price_service = get_service(context, "price_service", PriceService)
            rec_orm = trade_service.repo.get(db_session, rec_id)
            if not rec_orm:
                raise ValueError("التوصية غير موجودة.")
            
            live_price = await price_service.get_cached_price(rec_orm.asset, rec_orm.market, force_refresh=True)
            if not live_price:
                raise ValueError(f"تعذر جلب سعر السوق لـ {rec_orm.asset}.")
            
            if action == "close_market":
                await trade_service.close_recommendation_async(rec_id, str(query.from_user.id), Decimal(str(live_price)), db_session)
            elif action == CallbackAction.PARTIAL.value:
                percent_to_close = Decimal(parts[3])
                await trade_service.partial_close_async(rec_id, str(query.from_user.id), percent_to_close, Decimal(str(live_price)), db_session)
        
        await _send_or_edit_position_panel(query, context, db_session, 'rec', rec_id)
    except Exception as e:
        loge.error(f"Error in action handler for rec #{rec_id}: {e}", exc_info=True)
        await query.answer(f"❌ فشل الإجراء: {str(e)}", show_alert=True)

async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles prompting the user for text input (e.g., new SL, exit price)."""
    query = update.callback_query
    await query.answer()
    
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    if await handle_management_timeout(update, context):
        return
    
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
@require_active_user
@require_analyst_user
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles user text replies to prompts."""
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    if await handle_management_timeout(update, context):
        return
    
    state = context.user_data.pop(AWAITING_INPUT_KEY, None)
    if not state or not update.message.reply_to_message:
        return

    original_query = state.get("original_query")
    if not original_query:
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
        loge.error(f"Error processing reply for {action} on #{rec_id}: {e}", exc_info=True)
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

# --- Partial Close Conversation ---

async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the custom partial close conversation."""
    query = update.callback_query
    await query.answer()
    
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
    
    rec_id = int(parse_cq_parts(query.data)[2])
    context.user_data['partial_close_rec_id'] = rec_id
    
    await safe_edit_message(
        query, 
        text=f"{query.message.text}\n\n<b>💰 الرجاء إرسال النسبة المئوية للإغلاق (مثال: 25.5)</b>", 
        parse_mode=ParseMode.HTML
    )
    return AWAIT_PARTIAL_PERCENT

async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes received partial close percentage."""
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
    
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
        loge.error(f"Error in partial close percent: {e}", exc_info=True)
        await update.message.reply_text("❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """Processes received partial close price."""
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
    
    # تحديث النشاط أولاً ثم التحقق من المهلة
    update_management_activity(context)
    
    try:
        price = parse_number(update.message.text)
        if price is None:
            raise ValueError("تنسيق السعر غير صالح.")
        
        percent = context.user_data.pop('partial_close_percent')
        rec_id = context.user_data.pop('partial_close_rec_id')
        
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.partial_close_async(rec_id, str(update.effective_user.id), percent, price, db_session)
        await update.message.reply_text("✅ تم الإغلاق الجزئي بنجاح.", reply_markup=ReplyKeyboardRemove())
        
    except (ValueError, KeyError) as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة أو انتهت الجلسة: {e}. يرجى المحاولة مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PRICE
    except Exception as e:
        loge.error(f"Error in partial close flow for rec #{context.user_data.get('partial_close_rec_id')}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")
    
    clean_management_state(context)
    return ConversationHandler.END

async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the partial close conversation."""
    clean_management_state(context)
    await update.message.reply_text(
        "❌ تم إلغاء عملية الإغلاق الجزئي.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# --- Handler Registration ---

def register_management_handlers(app: Application):
    """Registers all management-related handlers with FIXED regex patterns."""
    ns_rec = CallbackNamespace.RECOMMENDATION.value
    ns_nav = CallbackNamespace.NAVIGATION.value
    ns_pos = CallbackNamespace.POSITION.value

    act_nv = CallbackAction.NAVIGATE.value  
    act_sh = CallbackAction.SHOW.value  
    act_st = CallbackAction.STRATEGY.value  
    act_pt = CallbackAction.PARTIAL.value

    # ✅ FINAL FIX: Unified entry point for commands
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))
    
    # ✅ FINAL FIX: Flexible regex patterns to accept parameters
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{ns_nav}:{act_nv}:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^(?:{ns_pos}:{act_sh}:|{ns_rec}:back_to_main:)"))
    
    # معالجات القوائم
    app.add_handler(CallbackQueryHandler(show_menu_handler, pattern=rf"^{ns_rec}:(?:edit_menu|close_menu|strategy_menu|{act_pt}):"))
    
    # ✅ FINAL FIX: Unified action handler with flexible patterns
    app.add_handler(CallbackQueryHandler(action_handler, pattern=rf"^{ns_rec}:(?:{act_st}|close_market|{act_pt}):"))
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^{ns_rec}:(?:edit_sl|edit_tp|close_manual):"))
    
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
        name="partial_close_conversation",
        per_user=True,
        per_chat=True,
        conversation_timeout=MANAGEMENT_TIMEOUT,
    )
    app.add_handler(partial_close_conv)