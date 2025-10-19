# src/capitalguard/interfaces/telegram/management_handlers.py (v29.2 - Production Ready & Final)
"""
إصدار إنتاجي نهائي لإدارة التوصيات والصفقات.
✅ إصلاح حاسم: إضافة استيراد 'CommandHandler' المفقود الذي كان يمنع بدء تشغيل التطبيق.
✅ إصلاح جذري لمشكلة "انتهاء مدة الجلسة" عبر تهيئة الجلسة بشكل صريح.
✅ تطبيق نظام مهلات قوي وموثوق.
✅ تكامل كامل مع بنية CallbackBuilder الموحدة.
✅ تحسين معالجة الأخطاء لضمان تجربة مستخدم سلسة ومستقرة.
"""

import logging
import time
from decimal import Decimal

from telegram import Update, ReplyKeyboardRemove, CallbackQuery
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, 
    ContextTypes, filters, ConversationHandler, CommandHandler  # ✅ الإصلاح الحاسم: تمت إضافة CommandHandler
)

from capitalguard.domain.entities import ExitStrategy
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
MANAGEMENT_TIMEOUT = 1800  # 30 دقيقة

# --- Session & Timeout Management ---

def init_management_session(context: ContextTypes.DEFAULT_TYPE):
    """تهيئة أو إعادة تعيين جلسة الإدارة لضمان بداية نظيفة."""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop('partial_close_rec_id', None)
    context.user_data.pop('partial_close_percent', None)
    log.debug(f"Management session initialized/reset for user {context._user_id}.")

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    """تحديث وقت النشاط الأخير للإدارة."""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف حالة الإدارة عند انتهاء المحادثة أو المهلة."""
    for key in [AWAITING_INPUT_KEY, LAST_ACTIVITY_KEY, 'partial_close_rec_id', 'partial_close_percent']:
        context.user_data.pop(key, None)

async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """معالجة انتهاء مدة جلسة الإدارة."""
    last_activity = context.user_data.get(LAST_ACTIVITY_KEY, 0)
    if time.time() - last_activity > MANAGEMENT_TIMEOUT:
        clean_management_state(context)
        msg = "⏰ انتهت مدة الجلسة بسبب عدم النشاط.\n\nيرجى استخدام /myportfolio للبدء من جديد."
        if update.callback_query:
            await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
            await safe_edit_message(update.callback_query, text=msg)
        elif update.message:
            await update.message.reply_text(msg)
        return True
    return False

# --- Helper Functions ---

async def safe_edit_message(query: CallbackQuery, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool:
    """تحرير الرسالة بشكل آمن مع استعادة الأخطاء."""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower(): return True
        loge.warning(f"Handled BadRequest in safe_edit_message: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError in safe_edit_message: {e}")
        return False

async def _send_or_edit_position_panel(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int):
    """إرسال أو تعديل لوحة المركز بشكل آمن وموحد."""
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), position_type, position_id)
        
        if not position:
            await safe_edit_message(query, text="❌ المركز غير موجود أو تم إغلاقه.")
            return

        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
        if live_price: setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)
        keyboard = build_user_trade_control_keyboard(position_id) if getattr(position, 'is_user_trade', False) else analyst_control_panel_keyboard(position)
        
        await safe_edit_message(query, text=text, reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True)
        await safe_edit_message(query, text=f"❌ خطأ في تحميل البيانات: {str(e)}")

# --- Entry Point Handlers (Commands) ---

@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """نقطة دخول موحدة لأوامر /myportfolio و /open."""
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
    """معالجة التنقل بين صفحات المراكز المفتوحة."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    
    page = int(parse_cq_parts(query.data)[2])
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
        keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        await safe_edit_message(query, text="<b>📊 المراكز المفتوحة</b>\nاختر مركزاً للإدارة:", reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error in open positions navigation: {e}", exc_info=True)
        await safe_edit_message(query, text="❌ خطأ في تحميل المراكز.")

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة عرض لوحة التحكم لمركز محدد."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    
    parts = parse_cq_parts(query.data)
    try:
        position_type, position_id = (parts[2], int(parts[3])) if parts[1] == CallbackAction.SHOW.value else ('rec', int(parts[2]))
        await _send_or_edit_position_panel(query, context, db_session, position_type, position_id)
    except (IndexError, ValueError) as e:
        loge.error(f"Could not parse position info from callback: {query.data}, error: {e}")
        await safe_edit_message(query, text="❌ بيانات استدعاء غير صالحة.")

@uow_transaction
@require_active_user
@require_analyst_user
async def show_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة عرض القوائم الفرعية (تعديل, إغلاق, استراتيجية)."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    
    parts = parse_cq_parts(query.data)
    action, rec_id = parts[1], int(parts[2])
    
    try:
        if action == "edit_menu": keyboard = analyst_edit_menu_keyboard(rec_id)
        elif action == "close_menu": keyboard = build_close_options_keyboard(rec_id)
        elif action == "strategy_menu":
            rec = get_service(context, "trade_service", TradeService).repo.get(db_session, rec_id)
            keyboard = build_exit_strategy_keyboard(get_service(context, "trade_service", TradeService).repo._to_entity(rec)) if rec else None
        elif action == CallbackAction.PARTIAL.value: keyboard = build_partial_close_keyboard(rec_id)
        else: keyboard = None
        
        if keyboard: await safe_edit_message(query, reply_markup=keyboard)
        else: await query.answer("❌ تعذر تحميل القائمة.", show_alert=True)
    except Exception as e:
        loge.error(f"Error in menu handler for rec #{rec_id}: {e}", exc_info=True)
        await query.answer("❌ خطأ في تحميل القائمة.", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج موحد للإجراءات (تغيير استراتيجية, إغلاق بسعر السوق, إغلاق جزئي)."""
    query = update.callback_query
    await query.answer("جاري التنفيذ...")
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    
    parts = parse_cq_parts(query.data)
    action, rec_id = parts[1], int(parts[2])
    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        if action == CallbackAction.STRATEGY.value:
            strategy_value = parts[3]
            await trade_service.update_exit_strategy_async(rec_id, str(query.from_user.id), ExitStrategy(strategy_value), db_session)
        else: # close_market or partial_close
            price_service = get_service(context, "price_service", PriceService)
            rec_orm = trade_service.repo.get(db_session, rec_id)
            if not rec_orm: raise ValueError("التوصية غير موجودة.")
            live_price = await price_service.get_cached_price(rec_orm.asset, rec_orm.market, force_refresh=True)
            if not live_price: raise ValueError(f"تعذر جلب سعر السوق لـ {rec_orm.asset}.")
            
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
    """معالجة طلب الإدخال من المستخدم (تعديل SL/TP, إغلاق يدوي)."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    
    parts = parse_cq_parts(query.data)
    action, rec_id = parts[1], int(parts[2])
    
    prompts = {
        "edit_sl": "✏️ أرسل وقف الخسارة الجديد:",
        "edit_tp": "🎯 أرسل قائمة الأهداف الجديدة (e.g., 50k 52k@50):",
        "close_manual": "✍️ أرسل سعر الإغلاق النهائي:"
    }
    context.user_data[AWAITING_INPUT_KEY] = {"action": action, "rec_id": rec_id, "original_query": query}
    
    await safe_edit_message(query, text=f"{query.message.text_html}\n\n<b>{prompts.get(action, 'أرسل القيمة الجديدة:')}</b>")

@uow_transaction
@require_active_user
@require_analyst_user
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة ردود المستخدم على الطلبات."""
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    
    state = context.user_data.pop(AWAITING_INPUT_KEY, None)
    if not (state and update.message.reply_to_message and state.get("original_query")): return
    
    action, rec_id, original_query = state["action"], state["rec_id"], state["original_query"]
    user_input = update.message.text.strip()
    try: await update.message.delete()
    except Exception: pass

    trade_service = get_service(context, "trade_service", TradeService)
    try:
        if action == "close_manual":
            price = parse_number(user_input)
            if price is None: raise ValueError("تنسيق السعر غير صالح.")
            await trade_service.close_recommendation_async(rec_id, str(update.effective_user.id), price, db_session)
        elif action == "edit_sl":
            price = parse_number(user_input)
            if price is None: raise ValueError("تنسيق السعر غير صالح.")
            await trade_service.update_sl_for_user_async(rec_id, str(update.effective_user.id), price, db_session)
        elif action == "edit_tp":
            targets = parse_targets_list(user_input.split())
            if not targets: raise ValueError("تنسيق الأهداف غير صالح.")
            await trade_service.update_targets_for_user_async(rec_id, str(update.effective_user.id), targets, db_session)
        
        await _send_or_edit_position_panel(original_query, context, db_session, 'rec', rec_id)
    except Exception as e:
        loge.error(f"Error processing reply for {action} on #{rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطأ: {e}\n\nيرجى المحاولة مرة أخرى.")
        context.user_data[AWAITING_INPUT_KEY] = state # Restore state for retry

# --- Partial Close Conversation ---

async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بدء محادثة الإغلاق الجزئي المخصص."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)
    
    rec_id = int(parse_cq_parts(query.data)[2])
    context.user_data['partial_close_rec_id'] = rec_id
    await safe_edit_message(query, text=f"{query.message.text_html}\n\n<b>💰 أرسل النسبة المئوية للإغلاق (e.g., 25.5)</b>")
    return AWAIT_PARTIAL_PERCENT

async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة استلام نسبة الإغلاق الجزئي."""
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)
    
    try:
        percent = parse_number(update.message.text)
        if not (percent and 0 < percent <= 100): raise ValueError("النسبة يجب أن تكون بين 0 و 100.")
        context.user_data['partial_close_percent'] = percent
        await update.message.reply_html(f"✅ النسبة: {percent:g}%\n\n<b>الآن، أرسل سعر الإغلاق.</b>")
        return AWAIT_PARTIAL_PRICE
    except ValueError as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. حاول مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """معالجة استلام سعر الإغلاق الجزئي وإنهاء المحادثة."""
    if await handle_management_timeout(update, context): return ConversationHandler.END
    
    try:
        price = parse_number(update.message.text)
        if price is None: raise ValueError("تنسيق السعر غير صالح.")
        
        percent = context.user_data['partial_close_percent']
        rec_id = context.user_data['partial_close_rec_id']
        
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.partial_close_async(rec_id, str(update.effective_user.id), percent, price, db_session)
        await update.message.reply_text("✅ تم الإغلاق الجزئي بنجاح.", reply_markup=ReplyKeyboardRemove())
    except (ValueError, KeyError) as e:
        await update.message.reply_text(f"❌ خطأ: {e}. حاول مرة أخرى أو /cancel.")
        return AWAIT_PARTIAL_PRICE
    except Exception as e:
        loge.error(f"Error in partial close flow for rec #{context.user_data.get('partial_close_rec_id')}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}", reply_markup=ReplyKeyboardRemove())
    
    clean_management_state(context)
    return ConversationHandler.END

async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء محادثة الإغلاق الجزئي."""
    clean_management_state(context)
    await update.message.reply_text("❌ تم إلغاء عملية الإغلاق الجزئي.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- Handler Registration ---

def register_management_handlers(app: Application):
    """تسجيل جميع معالجات الإدارة."""
    ns_rec, ns_nav, ns_pos = CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.NAVIGATION.value, CallbackNamespace.POSITION.value
    
    # ✅ الإصلاح: استخدام معالج واحد لنقاط الدخول
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))
    
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{ns_nav}:{CallbackAction.NAVIGATE.value}:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^(?:{ns_pos}:{CallbackAction.SHOW.value}:|{ns_rec}:back_to_main:)"))
    app.add_handler(CallbackQueryHandler(show_menu_handler, pattern=rf"^{ns_rec}:(?:edit_menu|close_menu|strategy_menu|{CallbackAction.PARTIAL.value}$)"))
    app.add_handler(CallbackQueryHandler(action_handler, pattern=rf"^{ns_rec}:(?:{CallbackAction.STRATEGY.value}|close_market|{CallbackAction.PARTIAL.value}:)"))
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^{ns_rec}:(?:edit_sl|edit_tp|close_manual)"))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, reply_handler))

    partial_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_close_custom_start, pattern=rf"^{ns_rec}:partial_close_custom:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_percent_received)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_price_received)],
        },
        fallbacks=[CommandHandler("cancel", partial_close_cancel)],
        name="partial_close_conversation",
        per_user=True, per_chat=True, conversation_timeout=MANAGEMENT_TIMEOUT,
    )
    app.add_handler(partial_close_conv)