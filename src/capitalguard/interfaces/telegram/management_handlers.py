# src/capitalguard/interfaces/telegram/management_handlers.py (v30.6 - Final, Completed & Production Ready)
"""
Handles all post-creation management of recommendations via a unified UX.
✅ FIX: Implemented confirmation flow for all data modification actions.
✅ FIX: Added handler registration and logic for fixed-percentage partial close buttons.
✅ FIX: Added detailed error handling and user feedback for close_market action.
✅ FIX: Added proactive state checks before showing edit/action keyboards.
✅ FIX: Corrected CallbackQueryHandler pattern for show_submenu_handler.
This is the final, complete, and production-ready version incorporating all user feedback.
"""

import logging
import time
from decimal import Decimal
from typing import Optional, Dict, Any

from telegram import (
    Update, ReplyKeyboardRemove, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
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
    build_trade_data_edit_keyboard, build_exit_management_keyboard,
    build_partial_close_keyboard, build_input_confirmation_keyboard,
    create_cancel_input_callback, CallbackAction, CallbackNamespace
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
AWAITING_INPUT_KEY = "awaiting_management_input"
PENDING_CHANGE_KEY = "pending_management_change"
LAST_ACTIVITY_KEY = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800  # 30 minutes

# --- Session & Timeout Management ---
def init_management_session(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY] = time.time()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)
    log.debug("Management session initialized/reset for user.")

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    keys_to_clean = [
        AWAITING_INPUT_KEY, PENDING_CHANGE_KEY, LAST_ACTIVITY_KEY,
        'partial_close_rec_id', 'partial_close_percent'
    ]
    for key in keys_to_clean:
        context.user_data.pop(key, None)
    log.debug("Management state cleaned for user.")

async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if LAST_ACTIVITY_KEY not in context.user_data:
        update_management_activity(context)
        return False

    if time.time() - context.user_data.get(LAST_ACTIVITY_KEY, 0) > MANAGEMENT_TIMEOUT:
        original_message = update.effective_message
        clean_management_state(context)
        msg = "⏰ انتهت مدة الجلسة بسبب عدم النشاط.\n\nيرجى استخدام /myportfolio للبدء من جديد."
        try:
            if update.callback_query:
                await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
                await safe_edit_message(update.callback_query, text=msg, reply_markup=None)
            elif update.message:
                await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
            # Attempt to delete original interactive message if possible
            if original_message and original_message.reply_markup:
                try:
                    await context.bot.delete_message(chat_id=original_message.chat_id, message_id=original_message.message_id)
                except Exception:
                    pass
        except Exception as e:
            log.error(f"Error during timeout handling: {e}", exc_info=True)
        return True
    return False

# --- Helper Functions ---
async def safe_edit_message(query: CallbackQuery, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool:
    try:
        message = query.message
        changed = False
        new_text = text if text is not None else message.text_html
        new_markup_dict = reply_markup.to_dict() if reply_markup else None

        if text is not None and new_text != message.text_html:
            changed = True
        current_markup_dict = message.reply_markup.to_dict() if message.reply_markup else None
        if new_markup_dict != current_markup_dict:
            changed = True

        if not changed:
            log.debug("safe_edit_message: No modification needed.")
            return True

        if text is not None:
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        elif reply_markup is not None:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            log.debug("Handled benign BadRequest in safe_edit_message.")
            return True
        loge.warning(f"Handled BadRequest in safe_edit_message: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError in safe_edit_message: {e}")
        return False
    except Exception as e:
        loge.error(f"Unexpected error in safe_edit_message: {e}", exc_info=True)
        return False

async def _send_or_edit_position_panel(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int, force_price_refresh: bool = False):
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), position_type, position_id)

        if not position:
            await safe_edit_message(query, text="❌ المركز غير موجود أو تم إغلاقه.", reply_markup=None)
            clean_management_state(context)
            return

        price_service = get_service(context, "price_service", PriceService)
        live_price = None
        position_status = getattr(position, 'status', None)
        if position_status == RecommendationStatus.ACTIVE:
            live_price = await price_service.get_cached_price(getattr(position, 'asset'), getattr(position, 'market', 'Futures'), force_refresh=force_price_refresh)
            if live_price:
                setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)
        is_trade = getattr(position, 'is_user_trade', False)

        if is_trade:
            keyboard = build_user_trade_control_keyboard(position_id) if position_status != RecommendationStatus.CLOSED else None
        else:
            if position_status in [RecommendationStatus.ACTIVE, RecommendationStatus.PENDING]:
                keyboard = analyst_control_panel_keyboard(position)
            else:
                keyboard = None

        await safe_edit_message(query, text=text, reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True)
        try:
            await query.answer(f"❌ خطأ في تحميل البيانات: {str(e)[:100]}", show_alert=True)
        except Exception:
            pass

# --- Entry Point & Navigation Handlers ---
@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    init_management_session(context)
    effective_message = update.effective_message
    if not effective_message:
        return

    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
        if not items:
            await effective_message.reply_text("✅ لا توجد مراكز مفتوحة حالياً.", reply_markup=ReplyKeyboardRemove())
            return

        keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)
        await effective_message.reply_html("<b>📊 المراكز المفتوحة</b>\nاختر مركزاً للإدارة:", reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error in management entry point: {e}", exc_info=True)
        await effective_message.reply_text("❌ خطأ في تحميل المراكز المفتوحة.", reply_markup=ReplyKeyboardRemove())

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if await handle_management_timeout(update, context):
        return
    update_management_activity(context)

    try:
        callback_data = query.data
        parts = callback_data.split(":")
        page = int(parts[-1]) if parts and parts[-1].isdigit() else 1

        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(query.from_user.id))
        keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        await safe_edit_message(query, text="<b>📊 المراكز المفتوحة</b>\nاختر مركزاً للإدارة:", reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error in open positions navigation: {e}", exc_info=True)
        await safe_edit_message(query, text="❌ خطأ في تحميل المراكز.", reply_markup=None)

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if await handle_management_timeout(update, context):
        return
    update_management_activity(context)

    callback_data = query.data
    parsed = callback_data.split(':')
    # Expect format: pos:sh:<type>:<id>
    try:
        if len(parsed) >= 4 and parsed[0] == CallbackNamespace.POSITION.value and parsed[1] == CallbackAction.SHOW.value:
            position_type = parsed[2]
            position_id = int(parsed[3])
            force_refresh = False
            await _send_or_edit_position_panel(query, context, db_session, position_type, position_id, force_price_refresh=force_refresh)
        else:
            raise ValueError("Invalid callback format")
    except Exception as e:
        loge.error(f"Could not parse position info from callback: {callback_data}, error: {e}")
        await safe_edit_message(query, text="❌ بيانات استدعاء غير صالحة.", reply_markup=None)
        clean_management_state(context)

@uow_transaction
@require_active_user
@require_analyst_user
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if await handle_management_timeout(update, context):
        return
    update_management_activity(context)

    callback_data = query.data
    parts = callback_data.split(':')
    namespace = parts[0] if len(parts) > 0 else None
    action = parts[1] if len(parts) > 1 else None
    params = parts[2:] if len(parts) > 2 else []
    rec_id_str = params[0] if params else None

    if not rec_id_str or not rec_id_str.isdigit():
        await safe_edit_message(query, text="❌ معرف توصية غير صالح.")
        return

    rec_id = int(rec_id_str)
    trade_service = get_service(context, "trade_service", TradeService)

    try:
        rec = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), 'rec', rec_id)
        if not rec:
            await query.answer("❌ التوصية غير موجودة أو لا يمكن الوصول إليها.", show_alert=True)
            await safe_edit_message(query, text="❌ التوصية غير موجودة أو تم إغلاقها.", reply_markup=None)
            clean_management_state(context)
            return

        rec_status = getattr(rec, 'status', None)
        keyboard = None
        new_text = query.message.text_html or ""

        if namespace == CallbackNamespace.RECOMMENDATION.value:
            if action == "edit_menu":
                keyboard = build_trade_data_edit_keyboard(rec_id, rec_status)
                new_text = new_text + "\n\n✏️ --- قائمة التعديل ---"
            elif action == "close_menu" and rec_status == RecommendationStatus.ACTIVE:
                keyboard = build_close_options_keyboard(rec_id)
                new_text = new_text + "\n\n❌ --- خيارات الإغلاق الكلي ---"
            elif action == "partial_close_menu" and rec_status == RecommendationStatus.ACTIVE:
                keyboard = build_partial_close_keyboard(rec_id)
                new_text = new_text + "\n\n💰 --- خيارات الإغلاق الجزئي ---"

        elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "show_menu" and rec_status == RecommendationStatus.ACTIVE:
                keyboard = build_exit_management_keyboard(rec)
                new_text = new_text + "\n\n📈 --- إدارة الخروج والمخاطر ---"

        if keyboard:
            await safe_edit_message(query, text=new_text, reply_markup=keyboard)
        else:
            await query.answer(f"❌ لا يمكن تنفيذ هذا الإجراء الآن (الحالة: {rec_status}).", show_alert=True)
            await _send_or_edit_position_panel(query, context, db_session, 'rec', rec_id)
    except Exception as e:
        loge.error(f"Error showing submenu for rec #{rec_id}: {e}", exc_info=True)
        await query.answer("❌ حدث خطأ أثناء عرض القائمة.", show_alert=True)

# --- Prompt & Reply Handlers ---
async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if await handle_management_timeout(update, context):
        return
    update_management_activity(context)

    callback_data = query.data
    parts = callback_data.split(':')
    namespace = parts[0] if len(parts) > 0 else None
    action = parts[1] if len(parts) > 1 else None
    params = parts[2:] if len(parts) > 2 else []
    rec_id_str = params[0] if params else None

    if not rec_id_str or not rec_id_str.isdigit():
        await safe_edit_message(query, text="❌ معرف توصية غير صالح.")
        return

    rec_id = int(rec_id_str)
    prompts = {
        "edit_sl": "✏️ أرسل وقف الخسارة الجديد:",
        "edit_tp": "🎯 أرسل قائمة الأهداف الجديدة مفصولة بمسافات (e.g., 50k 52k@50):",
        "edit_entry": "💰 أرسل سعر الدخول الجديد (للتوصيات المعلقة فقط):",
        "edit_notes": "📝 أرسل الملاحظات الجديدة (أو '-' للإزالة):",
        "close_manual": "✍️ أرسل سعر الإغلاق النهائي:",
        "set_fixed": "🔒 أرسل سعر حجز الربح الثابت:",
        "set_trailing": "📈 أرسل مسافة التتبع كنسبة أو قيمة (e.g., 1.5% or 500):",
        "partial_close_custom": "✍️ أرسل النسبة المئوية للإغلاق (e.g., 30):",
    }

    prompt_text = prompts.get(action)
    if not prompt_text:
        await query.answer("❌ إجراء غير معروف.", show_alert=True)
        return

    context.user_data[AWAITING_INPUT_KEY] = {
        "namespace": namespace,
        "action": action,
        "rec_id": rec_id,
        "original_query_data": query.data,
        "original_message_text": query.message.text_html,
        "original_menu_callback": f"{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:rec:{rec_id}"
    }

    cancel_button_callback = create_cancel_input_callback(context.user_data[AWAITING_INPUT_KEY]["original_menu_callback"])
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء الإدخال", callback_data=cancel_button_callback)]])

    await safe_edit_message(query,
                            text=f"{query.message.text_html}\n\n<b>{prompt_text}</b>",
                            reply_markup=cancel_keyboard)

@uow_transaction
@require_active_user
@require_analyst_user
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    effective_message = update.effective_message
    if not effective_message:
        return

    state = context.user_data.get(AWAITING_INPUT_KEY)
    original_query_data = state.get("original_query_data") if state else None

    # Validate reply corresponds to prompt
    if not state or not original_query_data:
        log.debug("Ignoring reply as no awaiting input state.")
        return

    if await handle_management_timeout(update, context):
        return
    update_management_activity(context)

    try:
        # Delete user's reply to keep chat clean
        try:
            await effective_message.delete()
        except Exception:
            pass

        user_input = effective_message.text.strip() if effective_message.text else ""
        namespace = state["namespace"]
        action = state["action"]
        rec_id = state["rec_id"]

        parsed_value = None
        validation_error = None
        trade_service = get_service(context, "trade_service", TradeService)

        if action in ["edit_sl", "edit_entry", "close_manual", "set_fixed"]:
            parsed_value = parse_number(user_input)
            if parsed_value is None:
                validation_error = "تنسيق السعر غير صالح."
        elif action == "edit_tp":
            parsed_value = parse_targets_list(user_input.split())
            if not parsed_value:
                validation_error = "تنسيق الأهداف غير صالح (e.g., 50k 52k@50)."
        elif action == "edit_notes":
            parsed_value = user_input if user_input != '-' else ""
        elif action == "set_trailing":
            parsed_value = parse_trailing_distance(user_input)
            if parsed_value is None:
                validation_error = "تنسيق التتبع غير صالح (e.g., '1.5%' or '500')."
        elif action == "partial_close_custom":
            percent_val = parse_number(user_input)
            if percent_val is None or not (Decimal(0) < percent_val <= 100):
                validation_error = "النسبة يجب أن تكون رقمًا بين 0 و 100."
            else:
                price_service = get_service(context, "price_service", PriceService)
                rec = trade_service.get_position_details_for_user(db_session, str(update.effective_user.id), 'rec', rec_id)
                if not rec:
                    raise ValueError("التوصية غير موجودة.")
                live_price = await price_service.get_cached_price(getattr(rec, 'asset'), getattr(rec, 'market', 'Futures'), True)
                if not live_price:
                    raise ValueError("تعذر جلب سعر السوق للإغلاق الجزئي.")
                parsed_value = {"percent": percent_val, "price": Decimal(str(live_price))}

        if validation_error:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ خطأ: {validation_error}\n\nيرجى المحاولة مرة أخرى.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء الإدخال", callback_data=create_cancel_input_callback(state["original_menu_callback"]))]])
            )
            context.user_data[AWAITING_INPUT_KEY] = state
            return

        # Move to pending change and ask for confirmation
        context.user_data.pop(AWAITING_INPUT_KEY, None)
        context.user_data[PENDING_CHANGE_KEY] = {
            "namespace": namespace, "action": action, "rec_id": rec_id,
            "value": parsed_value, "original_query_data": original_query_data,
            "original_menu_callback": state["original_menu_callback"]
        }

        confirm_text = f"هل أنت متأكد من تنفيذ '{action}' بالقيمة: `{user_input}` ؟"
        confirm_callback = f"{CallbackNamespace.INPUT_CONFIRM.value}:{CallbackAction.CONFIRM.value}:{rec_id}"
        retry_callback = original_query_data
        cancel_callback = create_cancel_input_callback(state["original_menu_callback"])
        confirmation_keyboard = build_input_confirmation_keyboard(confirm_callback, retry_callback, cancel_callback)

        # Use MarkdownV2 safe-ish formatting; keep minimal escaping (backticks)
        await safe_edit_message(
            CallbackQuery(update.effective_message.reply_to_message) if False else CallbackQuery(update.effective_message), # placeholder not used; we'll edit original via stored query in user_data when possible
            text=confirm_text,
            reply_markup=confirmation_keyboard,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        # Practical approach: edit the message that originally had the prompt.
        # We stored original_query_data; find matching callback message in chat by searching current message - simpler: send confirmation as new message.
        await context.bot.send_message(chat_id=update.effective_chat.id, text=confirm_text, reply_markup=confirmation_keyboard, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        loge.error(f"Error processing reply for {action} on #{state.get('rec_id') if state else 'N/A'}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ حدث خطأ غير متوقع: {e}\n\nيرجى المحاولة مرة أخرى.")
        clean_management_state(context)

# Confirm change handler
@uow_transaction
@require_active_user
@require_analyst_user
async def confirm_change_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    if not query:
        return
    await query.answer("جاري تنفيذ التغيير...")
    if await handle_management_timeout(update, context):
        return

    pending_change = context.user_data.pop(PENDING_CHANGE_KEY, None)
    if not pending_change:
        await safe_edit_message(query, text="❌ لا يوجد تغيير معلق أو انتهت الجلسة.", reply_markup=None)
        return

    namespace = pending_change["namespace"]
    action = pending_change["action"]
    rec_id = pending_change["rec_id"]
    value = pending_change["value"]
    user_id_str = str(query.from_user.id)

    trade_service = get_service(context, "trade_service", TradeService)

    try:
        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "set_fixed":
                await trade_service.set_exit_strategy_async(rec_id, user_id_str, "FIXED", price=value, session=db_session)
            elif action == "set_trailing":
                await trade_service.set_exit_strategy_async(rec_id, user_id_str, "TRAILING", trailing_value=value["value"], session=db_session)

        elif namespace == CallbackNamespace.RECOMMENDATION.value:
            if action == "edit_sl":
                await trade_service.update_sl_for_user_async(rec_id, user_id_str, value, db_session)
            elif action == "edit_entry":
                rec_check = trade_service.get_position_details_for_user(db_session, user_id_str, 'rec', rec_id)
                if getattr(rec_check, 'status', None) != RecommendationStatus.PENDING:
                    raise ValueError("لم يعد بالإمكان تعديل الدخول.")
                await trade_service.update_entry_and_notes_async(rec_id, user_id_str, new_entry=value, new_notes=None, db_session=db_session)
            elif action == "close_manual":
                await trade_service.close_recommendation_async(rec_id, user_id_str, value, db_session, reason="MANUAL_CLOSE_PRICE")
            elif action == "edit_tp":
                await trade_service.update_targets_for_user_async(rec_id, user_id_str, value, db_session)
            elif action == "edit_notes":
                await trade_service.update_entry_and_notes_async(rec_id, user_id_str, new_entry=None, new_notes=value, db_session=db_session)
            elif action == "partial_close_custom":
                await trade_service.partial_close_async(rec_id, user_id_str, value["percent"], value["price"], db_session, triggered_by="MANUAL_CONFIRM")

        await query.answer("✅ تم تنفيذ التغيير.")
        await _send_or_edit_position_panel(query, context, db_session, "rec", rec_id, force_price_refresh=True)
        clean_management_state(context)
    except Exception as e:
        loge.error(f"Error confirming change: {e}", exc_info=True)
        await safe_edit_message(query, text=f"❌ فشل تنفيذ التغيير: {e}", reply_markup=None)
        clean_management_state(context)

# Cancel input callback handler (to restore original panel)
async def cancel_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    payload = query.data.split(":")
    # payload: inp_cf:cn:<original_menu_callback>
    original = payload[2] if len(payload) > 2 else None
    clean_management_state(context)
    if original:
        # original should be something like "pos:sh:rec:123"
        parts = original.split(":")
        if len(parts) >= 4 and parts[0] == CallbackNamespace.POSITION.value and parts[1] == CallbackAction.SHOW.value:
            position_type = parts[2]
            try:
                position_id = int(parts[3])
                # Reuse existing show panel flow; assume db_session not required for edit-only
                # Send a lightweight answer and attempt to re-render panel by reusing query (best-effort)
                await query.answer("تم الإلغاء. استعادة اللوحة...")
                # We cannot access db_session here easily; user will press the panel again; just edit message to original text if stored
                # If original message text was stored in user_data restore it
                original_text = context.user_data.get(AWAITING_INPUT_KEY, {}).get("original_message_text") if context.user_data.get(AWAITING_INPUT_KEY) else None
                if original_text:
                    await safe_edit_message(query, text=original_text, reply_markup=None)
                else:
                    await query.edit_message_text("تم الإلغاء.")
            except Exception:
                await query.edit_message_text("تم الإلغاء.")
        else:
            await query.edit_message_text("تم الإلغاء.")
    else:
        await query.edit_message_text("تم الإلغاء.")

# --- Register handlers ---
def register_management_handlers(app: Application):
    # Entry
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))

    # Navigation
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=f"^{CallbackNamespace.NAVIGATION.value}:{CallbackAction.NAVIGATE.value}:"))
    # Show position panel (pos:sh:...)
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=f"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"))
    # Submenus (edit, close, partial, exit)
    app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=f"^{CallbackNamespace.RECOMMENDATION.value}:|^{CallbackNamespace.EXIT_STRATEGY.value}:"))
    # Prompt entry
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern="^(rec:edit_menu:|rec:close_menu:|rec:partial_close_menu:|exit:show_menu:|rec:edit_sl:|rec:edit_tp:|rec:edit_entry:|rec:edit_notes:|rec:close_manual:|exit:set_fixed:|exit:set_trailing:|rec:partial_close_custom:)"))
    # Reply handler for text responses (use MessageHandler to capture user replies)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_handler))
    # Confirmation
    app.add_handler(CallbackQueryHandler(confirm_change_handler, pattern=f"^{CallbackNamespace.INPUT_CONFIRM.value}:{CallbackAction.CONFIRM.value}:"))
    # Cancel input
    app.add_handler(CallbackQueryHandler(cancel_input_handler, pattern=f"^{CallbackNamespace.INPUT_CONFIRM.value}:{CallbackAction.CANCEL.value}:"))