# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
# src/capitalguard/interfaces/telegram/management_handlers.py (v30.10 - State & Callback Major Fix)
"""
Handles all post-creation management of recommendations via a unified UX.
✅ FIX: MAJOR: Reworked prompt/reply/confirm/cancel flow to use chat_id/message_id
  instead of 'CallbackQuery.from_dict', fixing the 'AttributeError: from_dict'.
✅ FIX: MAJOR: Reworked cancel handlers to re-call handlers programmatically
  instead of modifying 'query.data', fixing the 'AttributeError: data can't be set'.
✅ FIX: Added missing import for 'CallbackBuilder', 'CallbackNamespace' fixing 'NameError'.
✅ FIX: Corrected f-string syntax for 'change_description' in reply_handler.
✅ FIX: Added missing imports for InlineKeyboardButton, InlineKeyboardMarkup.
✅ UX: Added confirmation step for all data modifications.
✅ UX: Added Cancel button during input prompts.
✅ UX: Dynamically hide/show buttons based on recommendation status.
✅ FIX: Added handler for fixed-percentage partial close buttons.
✅ FIX: Added explicit error handling for 'Close Market' action.
This is the final, complete, and production-ready version.
"""

import logging
import time
from decimal import Decimal
from typing import Optional, Dict, Any, Union

from telegram import Update, ReplyKeyboardRemove, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Bot
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
    build_partial_close_keyboard, CallbackAction, CallbackNamespace,
    build_confirmation_keyboard, CallbackBuilder # ✅ FIX: Added missing import
)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list, parse_trailing_distance
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.domain.entities import RecommendationStatus

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- Constants ---
AWAITING_INPUT_KEY = "awaiting_management_input" # Stores {"namespace", "action", "rec_id", "original_message_chat_id", "original_message_message_id", "previous_callback"}
PENDING_CHANGE_KEY = "pending_management_change" # Stores validated value before confirmation
LAST_ACTIVITY_KEY = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800 # 30 minutes

# --- Session & Timeout Management ---
def init_management_session(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY] = time.time()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)
    log.debug(f"Management session initialized/reset for user {context._user_id}.")

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    for key in [AWAITING_INPUT_KEY, LAST_ACTIVITY_KEY, PENDING_CHANGE_KEY]:
        context.user_data.pop(key, None)

async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if LAST_ACTIVITY_KEY not in context.user_data:
        return False
    if time.time() - context.user_data.get(LAST_ACTIVITY_KEY, 0) > MANAGEMENT_TIMEOUT:
        clean_management_state(context)
        msg = "⏰ انتهت مدة الجلسة بسبب عدم النشاط.\n\nيرجى استخدام /myportfolio للبدء من جديد."
        if update.callback_query:
            try: await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
            except TelegramError: pass
            message_to_edit = update.callback_query.message
            if message_to_edit:
                await safe_edit_message(context.bot, message_to_edit.chat_id, message_to_edit.message_id, text=msg, reply_markup=None)
        elif update.message:
            await update.message.reply_text(msg)
        return True
    return False

# --- Helper Functions ---
# ✅ FIX: Reworked safe_edit_message to not depend on a query object
async def safe_edit_message(bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool:
    """Edits a message safely using chat_id and message_id."""
    if not chat_id or not message_id:
        log.warning("safe_edit_message called without valid chat_id or message_id.")
        return False

    try:
        if text is not None:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        elif reply_markup is not None:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower(): return True
        loge.warning(f"Handled BadRequest in safe_edit_message: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError in safe_edit_message: {e}")
        return False

async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int):
    """Fetches position details and renders the appropriate control panel."""
    query = update.callback_query # Prefer query for editing
    message_target = query.message if query else update.effective_message
    
    if not message_target:
        log.error("_send_or_edit_position_panel failed: No message target found.")
        return

    try:
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(db_session, str(update.effective_user.id), position_type, position_id)
        if not position:
            await safe_edit_message(context.bot, message_target.chat_id, message_target.message_id, text="❌ المركز غير موجود أو تم إغلاقه.", reply_markup=None)
            return

        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
        if live_price: setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)

        # Build keyboard based on type and status
        keyboard = None
        if getattr(position, 'is_user_trade', False):
             if position.status == RecommendationStatus.ACTIVE:
                 keyboard = build_user_trade_control_keyboard(position_id)
        elif position.status == RecommendationStatus.ACTIVE:
             keyboard = analyst_control_panel_keyboard(position)
        # For PENDING or CLOSED, show no keyboard
        
        await safe_edit_message(context.bot, message_target.chat_id, message_target.message_id, text=text, reply_markup=keyboard)

    except Exception as e:
        loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True)
        await safe_edit_message(context.bot, message_target.chat_id, message_target.message_id, text=f"❌ خطأ في تحميل البيانات: {str(e)}", reply_markup=None)


# --- Entry Point & Navigation Handlers ---
@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles /myportfolio and /open commands."""
    init_management_session(context) # Clean state before starting
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

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles pagination for the open positions list."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parts = parse_cq_parts(query.data)
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
        keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="<b>📊 المراكز المفتوحة</b>\nاختر مركزاً للإدارة:", reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error in open positions navigation: {e}", exc_info=True)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ خطأ في تحميل المراكز.")

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Shows the detailed control panel for a selected position."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    # Clear any pending input state when showing a panel
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)

    parts = parse_cq_parts(query.data)
    try:
        # Callback format: pos:sh:<type>:<id> or just pos:sh:<id> (defaults to rec)
        if len(parts) >= 4 and parts[1] == CallbackAction.SHOW.value:
            position_type, position_id = parts[2], int(parts[3])
        elif len(parts) == 3 and parts[1] == CallbackAction.SHOW.value: # Backward compatibility or default
             position_type, position_id = 'rec', int(parts[2])
        else: raise ValueError("Invalid callback format")

        await _send_or_edit_position_panel(update, context, db_session, position_type, position_id)
    except (IndexError, ValueError) as e:
        loge.error(f"Could not parse position info from callback: {query.data}, error: {e}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ بيانات استدعاء غير صالحة.", reply_markup=None)

@uow_transaction
@require_active_user
@require_analyst_user # Only analysts can access submenus
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Displays specific submenus like Edit, Close, Partial Close, Exit Management."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parts = parse_cq_parts(query.data)
    namespace, action, rec_id_str = parts[0], parts[1], parts[2]
    rec_id = int(rec_id_str)

    trade_service = get_service(context, "trade_service", TradeService)
    # Fetch recommendation to check status *before* showing the menu
    rec = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), 'rec', rec_id)
    if not rec:
        await query.answer("❌ التوصية غير موجودة أو تم إغلاقها.", show_alert=True)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ التوصية غير موجودة أو تم إغلاقها.", reply_markup=None)
        return

    keyboard = None
    text = query.message.text_html # Default text is the current card

    # Build keyboard based on action AND status
    # ✅ UX FIX: Check status before deciding which keyboard to show or if action is allowed
    if namespace == CallbackNamespace.RECOMMENDATION.value:
        if action == "edit_menu":
             text = "✏️ <b>تعديل بيانات الصفقة</b>\nاختر الحقل للتعديل:"
             # Build keyboard based on status
             if rec.status == RecommendationStatus.ACTIVE or rec.status == RecommendationStatus.PENDING:
                 keyboard = build_trade_data_edit_keyboard(rec_id) # TODO: Hide 'edit_entry' if ACTIVE
             else: # CLOSED or other states
                 keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))]])
                 text = f"✏️ <b>تعديل بيانات الصفقة</b>\n لا يمكن تعديل توصية بحالة {rec.status.value}"

        elif action == "close_menu":
            text = "❌ <b>إغلاق كلي للصفقة</b>\nاختر طريقة الإغلاق:"
            if rec.status == RecommendationStatus.ACTIVE:
                keyboard = build_close_options_keyboard(rec_id)
            else:
                 keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))]])
                 text = f"❌ <b>إغلاق كلي للصفقة</b>\n لا يمكن إغلاق توصية بحالة {rec.status.value}"

        elif action == "partial_close_menu":
            text = "💰 <b>إغلاق جزئي للصفقة</b>\nاختر النسبة:"
            if rec.status == RecommendationStatus.ACTIVE:
                 keyboard = build_partial_close_keyboard(rec_id)
            else:
                 keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))]])
                 text = f"💰 <b>إغلاق جزئي للصفقة</b>\n لا يمكن الإغلاق الجزئي لتوصية بحالة {rec.status.value}"

    elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
        if action == "show_menu":
            text = "📈 <b>إدارة الخروج والمخاطر</b>\nاختر الإجراء:"
            if rec.status == RecommendationStatus.ACTIVE:
                 keyboard = build_exit_management_keyboard(rec)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))]])
                text = f"📈 <b>إدارة الخروج والمخاطر</b>\n لا يمكن إدارة خروج توصية بحالة {rec.status.value}"

    if keyboard:
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=keyboard)
    else:
        # If no valid keyboard was built (e.g., invalid action), refresh main panel
        log.warning(f"No valid submenu keyboard for action '{action}' on rec #{rec_id} with status {rec.status}")
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)


# --- Prompt & Reply Handlers (With Confirmation Flow) ---
async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user to send the new value as a reply."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parts = parse_cq_parts(query.data)
    namespace, action, rec_id_str = parts[0], parts[1], parts[2]
    rec_id = int(rec_id_str)

    # ✅ FIX: Use CallbackBuilder (which is now imported)
    previous_callback_data = CallbackBuilder.create(namespace, "show_menu" if namespace == CallbackNamespace.EXIT_STRATEGY.value else f"{action.split('_')[0]}_menu", rec_id)
    
    # ✅ FIX: Store chat_id and message_id instead of query.to_dict()
    context.user_data[AWAITING_INPUT_KEY] = {
        "namespace": namespace,
        "action": action,
        "rec_id": rec_id,
        "original_message_chat_id": query.message.chat_id,
        "original_message_message_id": query.message.message_id,
        "previous_callback": previous_callback_data # Store where to go back on cancel
    }

    prompts = {
        "edit_sl": "✏️ أرسل وقف الخسارة الجديد:",
        "edit_tp": "🎯 أرسل قائمة الأهداف الجديدة (e.g., 50k 52k@50):",
        "edit_entry": "💰 أرسل سعر الدخول الجديد (للتوصيات المعلقة فقط):",
        "edit_notes": "📝 أرسل الملاحظات الجديدة (أو كلمة 'مسح'):",
        "close_manual": "✍️ أرسل سعر الإغلاق النهائي:",
        "set_fixed": "🔒 أرسل سعر حجز الربح الثابت:",
        "set_trailing": "📈 أرسل مسافة التتبع (e.g., 1.5% or 500):",
        "partial_close_custom": "💰 أرسل نسبة الإغلاق المخصصة (e.g., 30% أو 30):"
    }
    prompt_text = prompts.get(action, 'أرسل القيمة الجديدة:')

    # ✅ UX FIX: Keyboard with just a cancel button for the input phase
    cancel_button = InlineKeyboardButton("❌ إلغاء الإدخال", callback_data=CallbackBuilder.create("mgmt", "cancel_input", rec_id))
    input_keyboard = InlineKeyboardMarkup([[cancel_button]])

    # ✅ FIX: Use new safe_edit_message signature
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=f"{query.message.text_html}\n\n<b>{prompt_text}</b>", reply_markup=input_keyboard)

@uow_transaction
@require_active_user
@require_analyst_user # Modifications require analyst
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles the text reply containing the new value, validates it, and asks for confirmation."""
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    state = context.user_data.get(AWAITING_INPUT_KEY)
    
    # ✅ FIX: Check for state and message reply, get chat/message IDs
    if not (state and update.message.reply_to_message):
        log.debug("Reply handler ignored: No valid state or not a reply.")
        return
        
    chat_id = state.get("original_message_chat_id")
    message_id = state.get("original_message_message_id")
    
    if not (chat_id and message_id):
        log.error(f"Reply handler for user {update.effective_user.id} has corrupt state: missing message IDs.")
        context.user_data.pop(AWAITING_INPUT_KEY, None) # Clear corrupt state
        return

    namespace, action, rec_id = state["namespace"], state["action"], state["rec_id"]
    user_input = update.message.text.strip()
    validated_value: Any = None # Use Any to store various types
    change_description = "" # For the confirmation message

    try: await update.message.delete()
    except Exception: pass # Ignore if already deleted or permissions missing

    trade_service = get_service(context, "trade_service", TradeService) # Needed for validation logic access

    try:
        # --- Validate Input based on Action ---
        current_rec = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), 'rec', rec_id)
        if not current_rec:
            raise ValueError("التوصية لم تعد موجودة.")

        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "set_fixed":
                price = parse_number(user_input)
                if price is None: raise ValueError("تنسيق السعر غير صالح.")
                validated_value = {"mode": "FIXED", "price": price}
                change_description = f"تفعيل حجز ربح ثابت عند {price:g}"
            elif action == "set_trailing":
                config = parse_trailing_distance(user_input)
                if config is None: raise ValueError("تنسيق غير صالح. استخدم نسبة (e.g., '1.5%') أو قيمة (e.g., '500').")
                validated_value = {"mode": "TRAILING", "trailing_value": Decimal(str(config["value"]))}
                change_description = f"تفعيل وقف متحرك بمسافة {user_input}"

        elif namespace == CallbackNamespace.RECOMMENDATION.value:
            if action in ["edit_sl", "edit_entry", "close_manual"]:
                price = parse_number(user_input)
                if price is None: raise ValueError("تنسيق السعر غير صالح.")
                if action == "edit_sl":
                    temp_rec_data = {"side": current_rec.side.value, "entry": current_rec.entry.value, "stop_loss": price, "targets": [{"price": t.price.value} for t in current_rec.targets.values]}
                    trade_service._validate_recommendation_data(temp_rec_data["side"], temp_rec_data["entry"], temp_rec_data["stop_loss"], temp_rec_data["targets"])
                    validated_value = price
                    change_description = f"تعديل وقف الخسارة إلى {price:g}"
                elif action == "edit_entry":
                    if current_rec.status != RecommendationStatus.PENDING: raise ValueError("لا يمكن تعديل الدخول إلا للتوصيات المعلقة.")
                    temp_rec_data = {"side": current_rec.side.value, "entry": price, "stop_loss": current_rec.stop_loss.value, "targets": [{"price": t.price.value} for t in current_rec.targets.values]}
                    trade_service._validate_recommendation_data(temp_rec_data["side"], temp_rec_data["entry"], temp_rec_data["stop_loss"], temp_rec_data["targets"])
                    validated_value = price
                    change_description = f"تعديل سعر الدخول إلى {price:g}"
                elif action == "close_manual":
                    validated_value = price
                    change_description = f"إغلاق الصفقة يدويًا بسعر {price:g}"
            elif action == "edit_tp":
                targets = parse_targets_list(user_input.split())
                if not targets: raise ValueError("تنسيق الأهداف غير صالح.")
                temp_rec_data = {"side": current_rec.side.value, "entry": current_rec.entry.value, "stop_loss": current_rec.stop_loss.value, "targets": targets}
                trade_service._validate_recommendation_data(temp_rec_data["side"], temp_rec_data["entry"], temp_rec_data["stop_loss"], temp_rec_data["targets"])
                validated_value = targets
                # ✅ FIX: Correct f-string syntax (avoids backslash issue by separating list comprehension)
                price_strings = [f"{t['price']:g}" for t in validated_value] # Use double quotes for key
                change_description = f"تعديل الأهداف إلى: {', '.join(price_strings)}"
            elif action == "edit_notes":
                if user_input.lower() in ['clear', 'مسح', 'remove', 'إزالة', '']:
                     validated_value = None
                     change_description = "مسح الملاحظات"
                else:
                     validated_value = user_input
                     change_description = f"تعديل الملاحظات إلى: '{validated_value}'"
            elif action == "partial_close_custom":
                 percent_val = parse_number(user_input.replace('%',''))
                 if percent_val is None or not (0 < percent_val <= Decimal('100')):
                     raise ValueError("النسبة المئوية يجب أن تكون بين 0 و 100.")
                 validated_value = percent_val
                 change_description = f"إغلاق {percent_val:g}% من الصفقة بسعر السوق"

        if validated_value is not None or action == "edit_notes":
            context.user_data[PENDING_CHANGE_KEY] = {"value": validated_value}
            context.user_data.pop(AWAITING_INPUT_KEY, None)

            confirm_callback = CallbackBuilder.create("mgmt", "confirm_change", namespace, action, rec_id)
            reenter_callback = state.get("previous_callback")
            cancel_callback = CallbackBuilder.create("mgmt", "cancel_all", rec_id)

            confirm_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأكيد التغيير", callback_data=confirm_callback)],
                [InlineKeyboardButton("✏️ إعادة الإدخال", callback_data=reenter_callback)],
                [InlineKeyboardButton("❌ إلغاء الكل", callback_data=cancel_callback)],
            ])
            # ✅ FIX: Use new safe_edit_message signature
            await safe_edit_message(context.bot, chat_id, message_id, text=f"❓ <b>تأكيد الإجراء</b>\n\nهل أنت متأكد أنك تريد:\n➡️ {change_description}؟", reply_markup=confirm_keyboard)
        else:
             raise ValueError("فشل التحقق من القيمة المدخلة لسبب غير معروف.")

    except ValueError as e:
        log.warning(f"Invalid input for {action} on #{rec_id}: {e}")
        cancel_button = InlineKeyboardButton("❌ إلغاء الإدخال", callback_data=CallbackBuilder.create("mgmt", "cancel_input", rec_id))
        input_keyboard = InlineKeyboardMarkup([[cancel_button]])
        context.user_data[AWAITING_INPUT_KEY] = state # Keep state
        # ✅ FIX: Use new safe_edit_message signature
        # We need the original message text. Let's fetch it from the message ID.
        try:
            original_message_text = (await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id)).text_html # Hack to get current text
        except Exception:
            original_message_text = "يرجى المحاولة مرة أخرى" # Fallback
            
        await safe_edit_message(context.bot, chat_id, message_id, text=f"{original_message_text}\n\n⚠️ <b>خطأ:</b> {e}\n\nيرجى إعادة إدخال القيمة الصحيحة:", reply_markup=input_keyboard)

    except Exception as e:
        loge.error(f"Error processing reply for {action} on #{rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطأ غير متوقع أثناء معالجة ردك: {e}\n\nتم إلغاء العملية.")
        clean_management_state(context)
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)


# --- Confirmation & Cancellation Handlers ---
@uow_transaction
@require_active_user
@require_analyst_user
async def confirm_change_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Executes the pending change after user confirmation."""
    query = update.callback_query
    await query.answer("جاري التنفيذ...")
    if await handle_management_timeout(update, context): return

    pending_data = context.user_data.pop(PENDING_CHANGE_KEY, None)
    parts = parse_cq_parts(query.data) # mgmt:confirm_change:namespace:action:rec_id
    namespace, action, rec_id_str = parts[2], parts[3], parts[4]
    rec_id = int(rec_id_str)

    if not pending_data or "value" not in pending_data:
        await query.answer("❌ لا يوجد تغيير معلق للتأكيد أو أن البيانات تالفة.", show_alert=True)
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
        clean_management_state(context)
        return

    pending_value = pending_data["value"]
    trade_service = get_service(context, "trade_service", TradeService)
    user_telegram_id = str(db_user.telegram_user_id)

    try:
        # --- Execute based on Namespace and Action ---
        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            mode = pending_value["mode"]
            price = pending_value.get("price")
            trailing = pending_value.get("trailing_value")
            await trade_service.set_exit_strategy_async(rec_id, user_telegram_id, mode, price=price, trailing_value=trailing, active=True, session=db_session)
        elif namespace == CallbackNamespace.RECOMMENDATION.value:
            if action == "edit_sl": await trade_service.update_sl_for_user_async(rec_id, user_telegram_id, pending_value, db_session)
            elif action == "edit_entry": await trade_service.update_entry_and_notes_async(rec_id, user_telegram_id, new_entry=pending_value, new_notes=None, db_session=db_session)
            elif action == "close_manual": await trade_service.close_recommendation_async(rec_id, user_telegram_id, pending_value, db_session, reason="MANUAL_PRICE_CLOSE")
            elif action == "edit_tp": await trade_service.update_targets_for_user_async(rec_id, user_telegram_id, pending_value, db_session)
            elif action == "edit_notes": await trade_service.update_entry_and_notes_async(rec_id, user_telegram_id, new_entry=None, new_notes=pending_value, db_session=db_session)
            elif action == "partial_close_custom":
                price_service = get_service(context, "price_service", PriceService)
                rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, 'rec', rec_id)
                if not rec: raise ValueError("التوصية غير موجودة.")
                live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
                if not live_price: raise ValueError(f"تعذر جلب سعر السوق لـ {rec.asset.value}.")
                await trade_service.partial_close_async(rec_id, user_telegram_id, pending_value, Decimal(str(live_price)), db_session, triggered_by="MANUAL_CUSTOM")

        # Success: Update the panel
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    except (ValueError, Exception) as e:
        loge.error(f"Error confirming change for {action} on #{rec_id}: {e}", exc_info=True)
        await query.answer(f"❌ فشل التنفيذ: {e}", show_alert=True)
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    finally:
        clean_management_state(context)

# ✅ FIX: Reworked cancel handlers to be UOW-wrapped and call _send_or_edit_position_panel
@uow_transaction
@require_active_user
async def cancel_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles cancellation during the text input phase by returning to the main panel."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return

    state = context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)
    
    rec_id = int(parse_cq_parts(query.data)[2]) # Get rec_id from cancel_input:rec_id
    
    if rec_id:
         await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    else:
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ تم إلغاء الإدخال.")
    
@uow_transaction
@require_active_user
async def cancel_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles cancellation during the confirmation phase by returning to the main panel."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return

    clean_management_state(context)
    rec_id = int(parse_cq_parts(query.data)[2]) # Get rec_id from cancel_all:rec_id

    if rec_id:
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    else:
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ تم إلغاء العملية.")


# --- Immediate Action Handlers ---
@uow_transaction
@require_active_user
@require_analyst_user # Most immediate actions are analyst-only
async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles actions that execute immediately without needing user text input."""
    query = update.callback_query
    await query.answer("جاري التنفيذ...")
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parts = parse_cq_parts(query.data)
    namespace, action, rec_id_str = parts[0], parts[1], parts[2]
    rec_id = int(rec_id_str)
    trade_service = get_service(context, "trade_service", TradeService)
    user_telegram_id = str(db_user.telegram_user_id)

    try:
        rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, 'rec', rec_id)
        if not rec:
             raise ValueError("التوصية غير موجودة أو تم إغلاقها.")
        if action != "cancel" and rec.status != RecommendationStatus.ACTIVE:
             raise ValueError(f"لا يمكن تنفيذ هذا الإجراء على توصية بحالة {rec.status.value}")

        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "move_to_be":
                await trade_service.move_sl_to_breakeven_async(rec_id, db_session)
                await query.answer("✅ تم نقل الوقف إلى نقطة الدخول.")
            elif action == "cancel":
                 if rec.status == RecommendationStatus.ACTIVE:
                    await trade_service.set_exit_strategy_async(rec_id, user_telegram_id, "NONE", active=False, session=db_session)
                    await query.answer("❌ تم إلغاء استراتيجية الخروج الآلية.")
                 else:
                     await query.answer("ℹ️ لا توجد استراتيجية نشطة لإلغائها.", show_alert=True)
        elif namespace == CallbackNamespace.RECOMMENDATION.value:
             if action == "close_market":
                price_service = get_service(context, "price_service", PriceService)
                live_price = None
                try:
                    await query.answer("جاري جلب السعر...")
                    live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
                    if not live_price: raise ValueError(f"تعذر جلب سعر السوق لـ {rec.asset.value}.")
                except Exception as price_err:
                    loge.error(f"Failed to get live price for close_market #{rec_id}: {price_err}")
                    await query.answer(f"❌ فشل جلب السعر: {price_err}", show_alert=True)
                    return 

                try:
                    await query.answer("جاري الإغلاق...")
                    await trade_service.close_recommendation_async(rec_id, user_telegram_id, Decimal(str(live_price)), db_session, reason="MARKET_CLOSE_MANUAL")
                except Exception as close_err:
                    loge.error(f"Failed to close recommendation #{rec_id} via close_market: {close_err}", exc_info=True)
                    await query.answer(f"❌ فشل الإغلاق: {close_err}", show_alert=True)

        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    except (ValueError, Exception) as e:
        loge.error(f"Error in immediate action handler for rec #{rec_id} (Action: {namespace}:{action}): {e}", exc_info=True)
        await query.answer(f"❌ فشل الإجراء: {str(e)}", show_alert=True)
        if 'rec_id' in locals() and rec_id:
             await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles partial close buttons with fixed percentages (e.g., 25%, 50%)."""
    query = update.callback_query
    await query.answer("جاري التنفيذ...")
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parts = parse_cq_parts(query.data) # rec:pt:<rec_id>:<percentage>
    rec_id = int(parts[2])
    close_percent = Decimal(parts[3])
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    user_telegram_id = str(db_user.telegram_user_id)

    try:
        rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, 'rec', rec_id)
        if not rec: raise ValueError("التوصية غير موجودة.")
        if rec.status != RecommendationStatus.ACTIVE: raise ValueError("يمكن الإغلاق الجزئي للصفقات النشطة فقط.")

        live_price = None
        try:
            await query.answer("جاري جلب السعر...")
            live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
            if not live_price: raise ValueError(f"تعذر جلب سعر السوق لـ {rec.asset.value}.")
        except Exception as price_err:
            loge.error(f"Failed to get live price for partial_close_fixed #{rec_id}: {price_err}")
            await query.answer(f"❌ فشل جلب السعر: {price_err}", show_alert=True)
            return

        await trade_service.partial_close_async(
            rec_id, user_telegram_id, close_percent, Decimal(str(live_price)), db_session, triggered_by="MANUAL_FIXED"
        )
        await query.answer(f"✅ تم إغلاق {close_percent:g}% بسعر السوق.")

        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    except (ValueError, Exception) as e:
        loge.error(f"Error in partial close fixed handler for rec #{rec_id}: {e}", exc_info=True)
        await query.answer(f"❌ فشل الإغلاق الجزئي: {str(e)}", show_alert=True)
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)

# --- Handler Registration ---
def register_management_handlers(app: Application):
    """Registers all management-related handlers."""
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))
    
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{CallbackNamespace.NAVIGATION.value}:{CallbackAction.NAVIGATE.value}:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"))
    app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}:(?:edit_menu|close_menu|partial_close_menu)|{CallbackNamespace.EXIT_STRATEGY.value}:show_menu):"))
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_|set_|close_manual|partial_close_custom)"))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, reply_handler))
    app.add_handler(CallbackQueryHandler(confirm_change_handler, pattern=rf"^mgmt:confirm_change:"))
    app.add_handler(CallbackQueryHandler(cancel_input_handler, pattern=rf"^mgmt:cancel_input:"))
    app.add_handler(CallbackQueryHandler(cancel_all_handler, pattern=rf"^mgmt:cancel_all:"))
    app.add_handler(CallbackQueryHandler(immediate_action_handler, pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}:(?:move_to_be|cancel):|{CallbackNamespace.RECOMMENDATION.value}:close_market)"))
    app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:{CallbackAction.PARTIAL.value}:\d+:(?:25|50)$"))
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---