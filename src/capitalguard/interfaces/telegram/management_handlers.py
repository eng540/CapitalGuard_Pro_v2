# src/capitalguard/interfaces/telegram/management_handlers.py (v30.3 - Final Restoration Fix)
"""
Handles all post-creation management of recommendations via a unified UX.
✅ HOTFIX: Restored missing handler functions (`navigate_open_positions_handler`, etc.)
that were accidentally removed, fixing a critical startup NameError.
✅ Implements the full workflow for setting and managing exit strategies.
"""

import logging
import time
from decimal import Decimal
from typing import Optional

from telegram import Update, ReplyKeyboardRemove, CallbackQuery
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, 
    ContextTypes, filters, ConversationHandler, CommandHandler
)

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_cq_parts
from .keyboards import (
    analyst_control_panel_keyboard, build_open_recs_keyboard, 
    build_user_trade_control_keyboard, build_close_options_keyboard, 
    build_trade_data_edit_keyboard,
    build_exit_management_keyboard,
    build_partial_close_keyboard, CallbackAction, CallbackNamespace
)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list, parse_trailing_distance
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- Constants ---
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)
AWAITING_INPUT_KEY = "awaiting_management_input"
LAST_ACTIVITY_KEY = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800

# --- Session & Timeout Management ---
def init_management_session(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY] = time.time()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    log.debug(f"Management session initialized/reset for user {context._user_id}.")

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    for key in [AWAITING_INPUT_KEY, LAST_ACTIVITY_KEY, 'partial_close_rec_id', 'partial_close_percent']:
        context.user_data.pop(key, None)

async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if LAST_ACTIVITY_KEY not in context.user_data:
        return False
    if time.time() - context.user_data.get(LAST_ACTIVITY_KEY, 0) > MANAGEMENT_TIMEOUT:
        clean_management_state(context)
        msg = "⏰ انتهت مدة الجلسة بسبب عدم النشاط.\n\nيرجى استخدام /myportfolio للبدء من جديد."
        if update.callback_query:
            await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
            await safe_edit_message(update.callback_query, text=msg, reply_markup=None)
        elif update.message:
            await update.message.reply_text(msg)
        return True
    return False

# --- Helper Functions ---
async def safe_edit_message(query: CallbackQuery, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool:
    try:
        if text is not None:
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        elif reply_markup is not None:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower(): return True
        loge.warning(f"Handled BadRequest in safe_edit_message: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError in safe_edit_message: {e}")
        return False

async def _send_or_edit_position_panel(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int):
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), position_type, position_id)
        if not position:
            await safe_edit_message(query, text="❌ المركز غير موجود أو تم إغلاقه.", reply_markup=None)
            return
        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
        if live_price: setattr(position, "live_price", live_price)
        text = build_trade_card_text(position)
        keyboard = build_user_trade_control_keyboard(position_id) if getattr(position, 'is_user_trade', False) else analyst_control_panel_keyboard(position)
        await safe_edit_message(query, text=text, reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True)
        await safe_edit_message(query, text=f"❌ خطأ في تحميل البيانات: {str(e)}", reply_markup=None)

# --- Entry Point & Navigation Handlers ---
@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
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

# ✅ RESTORED HANDLER
@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    update_management_activity(context)
    if await handle_management_timeout(update, context): return
    
    parts = parse_cq_parts(query.data)
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    
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
    query = update.callback_query
    await query.answer()
    update_management_activity(context)
    if await handle_management_timeout(update, context): return
    parts = parse_cq_parts(query.data)
    try:
        position_type, position_id = (parts[2], int(parts[3])) if parts[1] == CallbackAction.SHOW.value else ('rec', int(parts[2]))
        await _send_or_edit_position_panel(query, context, db_session, position_type, position_id)
    except (IndexError, ValueError) as e:
        loge.error(f"Could not parse position info from callback: {query.data}, error: {e}")
        await safe_edit_message(query, text="❌ بيانات استدعاء غير صالحة.", reply_markup=None)

@uow_transaction
@require_active_user
@require_analyst_user
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    update_management_activity(context)
    if await handle_management_timeout(update, context): return
    
    parts = parse_cq_parts(query.data)
    namespace, action, rec_id_str = parts[0], parts[1], parts[2]
    rec_id = int(rec_id_str)
    
    trade_service = get_service(context, "trade_service", TradeService)
    rec = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), 'rec', rec_id)
    if not rec:
        await query.answer("❌ التوصية غير موجودة.", show_alert=True)
        return

    keyboard = None
    if namespace == CallbackNamespace.RECOMMENDATION.value:
        if action == "edit_menu": keyboard = build_trade_data_edit_keyboard(rec_id)
        elif action == "close_menu": keyboard = build_close_options_keyboard(rec_id)
        elif action == "partial_close_menu": keyboard = build_partial_close_keyboard(rec_id)
    elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
        if action == "show_menu": keyboard = build_exit_management_keyboard(rec)

    if keyboard:
        await safe_edit_message(query, reply_markup=keyboard)
    else:
        await query.answer("❌ قائمة غير معروفة.", show_alert=True)

# --- Prompt & Reply Handlers ---
async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    update_management_activity(context)
    if await handle_management_timeout(update, context): return
    
    parts = parse_cq_parts(query.data)
    namespace, action, rec_id_str = parts[0], parts[1], parts[2]
    rec_id = int(rec_id_str)
    
    prompts = {
        "edit_sl": "✏️ أرسل وقف الخسارة الجديد:",
        "edit_tp": "🎯 أرسل قائمة الأهداف الجديدة (e.g., 50k 52k@50):",
        "edit_entry": "💰 أرسل سعر الدخول الجديد (للتوصيات المعلقة فقط):",
        "edit_notes": "📝 أرسل الملاحظات الجديدة:",
        "close_manual": "✍️ أرسل سعر الإغلاق النهائي:",
        "set_fixed": "🔒 أرسل سعر حجز الربح الثابت:",
        "set_trailing": "📈 أرسل مسافة التتبع (e.g., 1.5% or 500):",
    }
    
    context.user_data[AWAITING_INPUT_KEY] = {"namespace": namespace, "action": action, "rec_id": rec_id, "original_query": query}
    await safe_edit_message(query, text=f"{query.message.text_html}\n\n<b>{prompts.get(action, 'أرسل القيمة الجديدة:')}</b>", reply_markup=None)

@uow_transaction
@require_active_user
@require_analyst_user
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    update_management_activity(context)
    if await handle_management_timeout(update, context): return
    
    state = context.user_data.pop(AWAITING_INPUT_KEY, None)
    if not (state and update.message.reply_to_message and state.get("original_query")): return
    
    namespace, action, rec_id, original_query = state["namespace"], state["action"], state["rec_id"], state["original_query"]
    user_input = update.message.text.strip()
    try: await update.message.delete()
    except Exception: pass

    trade_service = get_service(context, "trade_service", TradeService)
    try:
        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "set_fixed":
                price = parse_number(user_input)
                if price is None: raise ValueError("تنسيق السعر غير صالح.")
                await trade_service.set_exit_strategy_async(rec_id, str(update.effective_user.id), "FIXED", price=price, session=db_session)
            elif action == "set_trailing":
                config = parse_trailing_distance(user_input)
                if config is None: raise ValueError("تنسيق غير صالح. استخدم نسبة (e.g., '1.5%') أو قيمة (e.g., '500').")
                await trade_service.set_exit_strategy_async(rec_id, str(update.effective_user.id), "TRAILING", trailing_value=Decimal(str(config["value"])), session=db_session)
        
        elif namespace == CallbackNamespace.RECOMMENDATION.value:
            if action in ["edit_sl", "edit_entry", "close_manual"]:
                price = parse_number(user_input)
                if price is None: raise ValueError("تنسيق السعر غير صالح.")
                if action == "edit_sl": await trade_service.update_sl_for_user_async(rec_id, str(update.effective_user.id), price, db_session)
                elif action == "edit_entry": await trade_service.update_entry_and_notes_async(rec_id, str(update.effective_user.id), new_entry=price, new_notes=None, db_session=db_session)
                elif action == "close_manual": await trade_service.close_recommendation_async(rec_id, str(update.effective_user.id), price, db_session)
            elif action == "edit_tp":
                targets = parse_targets_list(user_input.split())
                if not targets: raise ValueError("تنسيق الأهداف غير صالح.")
                await trade_service.update_targets_for_user_async(rec_id, str(update.effective_user.id), targets, db_session)
            elif action == "edit_notes":
                await trade_service.update_entry_and_notes_async(rec_id, str(update.effective_user.id), new_entry=None, new_notes=user_input, db_session=db_session)

        await _send_or_edit_position_panel(original_query, context, db_session, 'rec', rec_id)
    except Exception as e:
        loge.error(f"Error processing reply for {action} on #{rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطأ: {e}\n\nيرجى المحاولة مرة أخرى.")
        context.user_data[AWAITING_INPUT_KEY] = state

# --- Immediate Action Handlers ---
@uow_transaction
@require_active_user
@require_analyst_user
async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer("جاري التنفيذ...")
    update_management_activity(context)
    if await handle_management_timeout(update, context): return

    parts = parse_cq_parts(query.data)
    namespace, action, rec_id_str = parts[0], parts[1], parts[2]
    rec_id = int(rec_id_str)
    trade_service = get_service(context, "trade_service", TradeService)

    try:
        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "move_to_be":
                await trade_service.move_sl_to_breakeven_async(rec_id, db_session)
            elif action == "cancel":
                await trade_service.set_exit_strategy_async(rec_id, str(query.from_user.id), "NONE", active=False, session=db_session)
        
        elif namespace == CallbackNamespace.RECOMMENDATION.value:
             if action == "close_market":
                price_service = get_service(context, "price_service", PriceService)
                rec = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), 'rec', rec_id)
                if not rec: raise ValueError("التوصية غير موجودة.")
                live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
                if not live_price: raise ValueError(f"تعذر جلب سعر السوق لـ {rec.asset.value}.")
                await trade_service.close_recommendation_async(rec_id, str(query.from_user.id), Decimal(str(live_price)), db_session)

        await _send_or_edit_position_panel(query, context, db_session, 'rec', rec_id)
    except Exception as e:
        loge.error(f"Error in immediate action handler for rec #{rec_id}: {e}", exc_info=True)
        await query.answer(f"❌ فشل الإجراء: {str(e)}", show_alert=True)

# --- Handler Registration ---
def register_management_handlers(app: Application):
    """Registers all management-related handlers."""
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))
    
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{CallbackNamespace.NAVIGATION.value}:{CallbackAction.NAVIGATE.value}:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"))
    app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):show_menu:"))
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_|set_|close_manual)"))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, reply_handler))
    app.add_handler(CallbackQueryHandler(immediate_action_handler, pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}:(?:move_to_be|cancel):|{CallbackNamespace.RECOMMENDATION.value}:close_market:)"))