--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
# src/capitalguard/interfaces/telegram/management_handlers.py (v30.16 - Debug logs added)
"""
Management handlers v30.16
Added debug logs to trace why analyst panel isn't shown.
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
from capitalguard.interfaces.telegram.helpers import get_service, parse_cq_parts, _get_attr
from capitalguard.interfaces.telegram.keyboards import (
    analyst_control_panel_keyboard, build_open_recs_keyboard,
    build_user_trade_control_keyboard, build_close_options_keyboard,
    build_trade_data_edit_keyboard, build_exit_management_keyboard,
    build_partial_close_keyboard, CallbackAction, CallbackNamespace,
    build_confirmation_keyboard, CallbackBuilder, ButtonTexts
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user, get_db_user
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list, parse_trailing_distance
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.models import UserTradeStatus, UserType as UserTypeEntity

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

AWAITING_INPUT_KEY = "awaiting_management_input"
PENDING_CHANGE_KEY = "pending_management_change"
LAST_ACTIVITY_KEY = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800

(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)
(AWAIT_USER_TRADE_CLOSE_PRICE,) = range(AWAIT_PARTIAL_PRICE + 1, AWAIT_PARTIAL_PRICE + 2)


def init_management_session(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY] = time.time()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)
    context.user_data.pop('partial_close_rec_id', None)
    context.user_data.pop('partial_close_percent', None)
    context.user_data.pop('user_trade_close_id', None)
    context.user_data.pop('user_trade_close_msg_id', None)
    context.user_data.pop('user_trade_close_chat_id', None)
    log.debug(f"Management session initialized/reset for user {context._user_id}.")

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    if LAST_ACTIVITY_KEY not in context.user_data:
         init_management_session(context)
    else:
         context.user_data[LAST_ACTIVITY_KEY] = time.time()

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    keys_to_pop = [
        AWAITING_INPUT_KEY, PENDING_CHANGE_KEY, LAST_ACTIVITY_KEY,
        'partial_close_rec_id', 'partial_close_percent',
        'user_trade_close_id', 'user_trade_close_msg_id', 'user_trade_close_chat_id'
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    log.debug("All management conversation states cleared.")

async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    last_activity = context.user_data.get(LAST_ACTIVITY_KEY, 0)
    if time.time() - last_activity > MANAGEMENT_TIMEOUT:
        msg = "⏰ Session expired due to inactivity.\nPlease use /myportfolio to start again."
        target_chat_id = update.effective_chat.id
        target_message_id = None
        if update.callback_query and update.callback_query.message:
            target_message_id = update.callback_query.message.message_id
            try: await update.callback_query.answer("Session expired", show_alert=True)
            except TelegramError: pass
        clean_management_state(context)
        if target_message_id:
            await safe_edit_message(context.bot, target_chat_id, target_message_id, text=msg, reply_markup=None)
        elif update.message:
            await update.message.reply_text(msg)
        else:
             await context.bot.send_message(chat_id=target_chat_id, text=msg)
        return True
    return False

async def safe_edit_message(bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool:
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
        loge.warning(f"Handled BadRequest editing msg {chat_id}:{message_id}: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError editing msg {chat_id}:{message_id}: {e}")
        return False
    except Exception as e:
         loge.exception(f"Unexpected error editing msg {chat_id}:{message_id}: {e}")
         return False

async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int):
    query = update.callback_query
    message_target = query.message if query and query.message else update.effective_message
    if not message_target:
        log.error(f"_send_or_edit_position_panel failed for {position_type} #{position_id}: No message target found.")
        await update.effective_chat.send_message("Error: Could not find the message to update.")
        return

    chat_id = message_target.chat_id
    message_id = message_target.message_id

    try:
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(
            db_session, str(update.effective_user.id), position_type, position_id
        )

        if not position:
            await safe_edit_message(context.bot, chat_id, message_id, text="❌ Position not found or has been closed.", reply_markup=None)
            return

        # --- DEBUG: log position status before building keyboard ---
        try:
            status_repr = repr(_get_attr(position, 'status'))
            status_value = _get_attr(_get_attr(position, 'status'), 'value')
            log.debug(f"DBG_PANEL: pos_type={position_type} pos_id={position_id} status_repr={status_repr} status_value={status_value} is_user_trade={getattr(position,'is_user_trade',False)}")
        except Exception as _e:
            log.debug(f"DBG_PANEL: failed to inspect position status for {position_type}#{position_id}: {_e}")

        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(
            _get_attr(position.asset, 'value'),
            _get_attr(position, 'market', 'Futures'),
            force_refresh=True
        )
        if live_price is not None:
            setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)
        keyboard = None

        is_trade = getattr(position, 'is_user_trade', False)
        if position.status == RecommendationStatus.ACTIVE:
            if is_trade:
                keyboard = build_user_trade_control_keyboard(position_id)
            else:
                keyboard = analyst_control_panel_keyboard(position)
        else:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))
            ]])

        await safe_edit_message(context.bot, chat_id, message_id, text=text, reply_markup=keyboard)

    except Exception as e:
        loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True)
        await safe_edit_message(context.bot, chat_id, message_id, text=f"❌ Error loading position data: {str(e)}", reply_markup=None)


@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    init_management_session(context)
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
        if not items:
            await update.message.reply_text("✅ No open positions found.")
            return
        keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)
        await update.message.reply_html("<b>📊 Open Positions</b>\nSelect a position to manage:", reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error in management entry point: {e}", exc_info=True)
        await update.message.reply_text("❌ Error loading open positions.")

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    parts = CallbackBuilder.parse(query.data).get('params', [])
    page = int(parts[0]) if parts and parts[0].isdigit() else 1
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
        keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="<b>📊 Open Positions</b>\nSelect a position to manage:", reply_markup=keyboard)
    except Exception as e:
        loge.error(f"Error navigating open positions (page {page}): {e}", exc_info=True)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Error loading positions page.", reply_markup=None)


@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)
    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get('params', [])
    try:
        if len(params) >= 2:
             position_type, position_id_str = params[0], params[1]
             position_id = int(position_id_str)
        else: raise ValueError("Insufficient parameters in callback")
        await _send_or_edit_position_panel(update, context, db_session, position_type, position_id)
    except (IndexError, ValueError, TypeError) as e:
        loge.error(f"Could not parse position info from callback: {query.data}, error: {e}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request data.", reply_markup=None)


@uow_transaction
@require_active_user
@require_analyst_user
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    parsed_data = CallbackBuilder.parse(query.data)
    namespace = parsed_data.get('namespace')
    action = parsed_data.get('action')
    params = parsed_data.get('params', [])
    rec_id = int(params[0]) if params and params[0].isdigit() else None
    if rec_id is None:
        loge.error(f"Could not get rec_id from submenu callback: {query.data}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request.", reply_markup=None)
        return
    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), 'rec', rec_id)
    if not position:
        await query.answer("❌ Recommendation not found or closed.", show_alert=True)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Recommendation not found or closed.", reply_markup=None)
        return

    # --- DEBUG: inspect position.status before building submenu ---
    try:
        s_repr = repr(_get_attr(position,'status'))
        s_value = _get_attr(_get_attr(position,'status'),'value')
        log.debug(f"DBG_PANEL: show_submenu_handler rec_id={rec_id} status_repr={s_repr} status_value={s_value}")
    except Exception as _e:
        log.debug(f"DBG_PANEL: show_submenu_handler failed to inspect status for rec {rec_id}: {_e}")

    keyboard = None
    text = query.message.text_html
    can_modify = position.status == RecommendationStatus.ACTIVE
    can_edit_pending = position.status == RecommendationStatus.PENDING
    back_button = InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))

    if namespace == CallbackNamespace.RECOMMENDATION.value:
        if action == "edit_menu":
             text = "✏️ <b>Edit Recommendation Data</b>\nSelect field to edit:"
             if position.status == RecommendationStatus.ACTIVE or position.status == RecommendationStatus.PENDING:
                 keyboard = build_trade_data_edit_keyboard(rec_id)
             else:
                 keyboard = InlineKeyboardMarkup([[back_button]])
                 text = f"✏️ <b>Edit Recommendation Data</b>\n Cannot edit a recommendation with status {position.status.value}"

        elif action == "close_menu":
            text = "❌ <b>Close Position Fully</b>\nSelect closing method:"
            if can_modify:
                keyboard = build_close_options_keyboard(rec_id)
            else:
                keyboard = InlineKeyboardMarkup([[back_button]])
                text = f"❌ <b>Close Position Fully</b>\n Cannot close a recommendation with status {position.status.value}"

        elif action == "partial_close_menu":
             text = "💰 <b>Partial Close Position</b>\nSelect percentage:"
            if can_modify:
                 keyboard = build_partial_close_keyboard(rec_id)
            else:
                 keyboard = InlineKeyboardMarkup([[back_button]])
                 text = f"💰 <b>Partial Close Position</b>\n Cannot partially close a recommendation with status {position.status.value}"

    elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
        if action == "show_menu":
             text = "📈 <b>Manage Exit & Risk</b>\nSelect action:"
             if can_modify: keyboard = build_exit_management_keyboard(position)
             else:
                keyboard = InlineKeyboardMarkup([[back_button]])
                text = f"📈 <b>Manage Exit & Risk</b>\n Cannot manage exit for recommendation with status {position.status.value}"

    if keyboard:
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=keyboard)
    else:
        log.warning(f"No valid submenu keyboard for action '{action}' on rec #{rec_id} with status {position.status}")
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)


# --- Prompt & Reply for Modifications (Mainly Analyst Actions) ---
async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data)
    namespace = parsed_data.get('namespace')
    action = parsed_data.get('action')
    params = parsed_data.get('params', [])
    rec_id = int(params[0]) if params and params[0].isdigit() else None

    if rec_id is None:
         loge.error(f"Could not get rec_id from prompt callback: {query.data}")
         await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request.", reply_markup=None)
         return

    context.user_data[AWAITING_INPUT_KEY] = {
        "namespace": namespace,
        "action": action,
        "item_id": rec_id,
        "item_type": 'rec',
        "original_message_chat_id": query.message.chat_id,
        "original_message_message_id": query.message.message_id,
        "previous_callback": CallbackBuilder.create(namespace, "show_menu" if namespace == CallbackNamespace.EXIT_STRATEGY.value else f"{action.split('_')[0]}_menu", rec_id)
    }

    prompts = {
        "edit_sl": "✏️ Send the new Stop Loss price:",
        "edit_tp": "🎯 Send the new list of Targets (e.g., 50k 52k@50):",
        "edit_entry": "💰 Send the new Entry price (only for PENDING):",
        "edit_notes": "📝 Send the new Notes (or send 'clear' to remove):",
        "close_manual": "✍️ Send the final Exit Price:",
        "set_fixed": "🔒 Send the fixed Profit Stop price:",
        "set_trailing": "📈 Send the Trailing Stop distance (e.g., 1.5% or 500):",
        "partial_close_custom": "💰 Send the custom partial close Percentage (e.g., 30):"
    }
    prompt_text = prompts.get(action, 'Send the new value:')

    cancel_button = InlineKeyboardButton(
        "❌ Cancel Input",
        callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id)
    )
    input_keyboard = InlineKeyboardMarkup([[cancel_button]])

    await safe_edit_message(
        context.bot, query.message.chat_id, query.message.message_id,
        text=f"{query.message.text_html}\n\n<b>{prompt_text}</b>",
        reply_markup=input_keyboard
    )

@uow_transaction
@require_active_user
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    state = context.user_data.get(AWAITING_INPUT_KEY)

    if not (state and update.message and update.message.reply_to_message):
        log.debug("Reply handler ignored: No valid state or not a reply.")
        return

    chat_id = state.get("original_message_chat_id")
    message_id = state.get("original_message_message_id")

    if not (chat_id and message_id):
        log.error(f"Reply handler for user {update.effective_user.id} has corrupt state: missing message IDs.")
        context.user_data.pop(AWAITING_INPUT_KEY, None)
        return

    namespace = state.get("namespace")
    action = state.get("action")
    item_id = state.get("item_id")
    item_type = state.get("item_type", 'rec')
    user_input = update.message.text.strip() if update.message.text else ""

    is_analyst_action = namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value]
    if is_analyst_action and (not db_user or db_user.user_type != UserTypeEntity.ANALYST):
         await update.message.reply_text("🚫 Permission Denied: This action requires Analyst role.")
         context.user_data.pop(AWAITING_INPUT_KEY, None)
         return

    try: await update.message.delete()
    except Exception: log.debug("Could not delete user reply message.")

    validated_value: Any = None
    change_description = ""
    error_message = None
    trade_service = get_service(context, "trade_service", TradeService)

    try:
        current_item = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), item_type, item_id)
        if not current_item: raise ValueError("Position not found or closed.")

        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "set_fixed":
                 price = parse_number(user_input)
                 if price is None: raise ValueError("Invalid price format.")
                 entry_dec = _get_attr(current_item.entry, 'value')
                 if (_get_attr(current_item.side, 'value') == 'LONG' and price <= entry_dec) or \
                    (_get_attr(current_item.side, 'value') == 'SHORT' and price >= entry_dec):
                      raise ValueError("Fixed profit stop price must be beyond entry price.")
                 validated_value = {"mode": "FIXED", "price": price}
                 change_description = f"Activate Fixed Profit Stop at {_format_price(price)}"
            elif action == "set_trailing":
                 config = parse_trailing_distance(user_input)
                 if config is None: raise ValueError("Invalid format. Use % (e.g., '1.5%') or value (e.g., '500').")
                 validated_value = {"mode": "TRAILING", "trailing_value": config["value"]}
                 change_description = f"Activate Trailing Stop with distance {user_input}"

        elif namespace == CallbackNamespace.RECOMMENDATION.value:
            if action in ["edit_sl", "edit_entry", "close_manual"]:
                price = parse_number(user_input)
                if price is None: raise ValueError("Invalid price format.")
                if action == "edit_sl":
                    trade_service._validate_recommendation_data(
                         _get_attr(current_item.side, 'value'), _get_attr(current_item.entry, 'value'), price, current_item.targets.values
                    )
                    validated_value = price
                    change_description = f"Update Stop Loss to {_format_price(price)}"
                elif action == "edit_entry":
                     if current_item.status != RecommendationStatus.PENDING: raise ValueError("Entry can only be edited for PENDING signals.")
                     trade_service._validate_recommendation_data(
                         _get_attr(current_item.side, 'value'), price, _get_attr(current_item.stop_loss, 'value'), current_item.targets.values
                     )
                     validated_value = price
                     change_description = f"Update Entry Price to {_format_price(price)}"
                elif action == "close_manual":
                     validated_value = price
                     change_description = f"Manually Close Position at {_format_price(price)}"
            elif action == "edit_tp":
                targets_list_dict = parse_targets_list(user_input.split())
                if not targets_list_dict: raise ValueError("Invalid targets format or no valid targets found.")
                trade_service._validate_recommendation_data(
                     _get_attr(current_item.side, 'value'), _get_attr(current_item, 'entry', 'value'),
                     _get_attr(current_item.stop_loss, 'value'), targets_list_dict
                )
                validated_value = targets_list_dict
                price_strings = [_format_price(t['price']) for t in validated_value]
                change_description = f"Update Targets to: {', '.join(price_strings)}"
            elif action == "edit_notes":
                 if user_input.lower() in ['clear', 'مسح', 'remove', 'إزالة', '']:
                      validated_value = None
                      change_description = "Clear Notes"
                 else:
                      validated_value = user_input
                      change_description = f"Update Notes to: '{_truncate_text(validated_value, 50)}'"
            elif action == "partial_close_custom":
                 percent_val = parse_number(user_input.replace('%',''))
                 if percent_val is None or not (0 < percent_val <= Decimal('100')):
                     raise ValueError("Percentage must be a number between 0 and 100.")
                 validated_value = percent_val
                 change_description = f"Partially Close {percent_val:g}% of position at Market Price"

        if validated_value is not None or action == "edit_notes":
            context.user_data[PENDING_CHANGE_KEY] = {"value": validated_value}
            context.user_data.pop(AWAITING_INPUT_KEY, None)

            confirm_callback = CallbackBuilder.create("mgmt", "confirm_change", namespace, action, item_id)
            reenter_callback = state.get("previous_callback", CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, item_id))
            cancel_callback = CallbackBuilder.create("mgmt", "cancel_all", item_id)

            confirm_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(ButtonTexts.CONFIRM, callback_data=confirm_callback)],
                [InlineKeyboardButton("✏️ Re-enter Value", callback_data=reenter_callback)],
                [InlineKeyboardButton(ButtonTexts.CANCEL + " Action", callback_data=cancel_callback)],
            ])
            await safe_edit_message(context.bot, chat_id, message_id, text=f"❓ <b>Confirm Action</b>\n\nDo you want to:\n➡️ {change_description}?", reply_markup=confirm_keyboard)
        else:
             raise ValueError("Validation passed but no value was stored.")

    except ValueError as e:
        log.warning(f"Invalid input during reply for {action} on {item_type} #{item_id}: {e}")
        cancel_button = InlineKeyboardButton("❌ Cancel Input", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", item_id))
        prompts = {
            "edit_sl": "✏️ Send the new Stop Loss price:",
            "edit_tp": "🎯 Send the new list of Targets (e.g., 50k 52k@50):",
            "edit_entry": "💰 Send the new Entry price (only for PENDING):",
            "edit_notes": "📝 Send the new Notes (or send 'clear' to remove):",
            "close_manual": "✍️ Send the final Exit Price:",
            "set_fixed": "🔒 Send the fixed Profit Stop price:",
            "set_trailing": "📈 Send the Trailing Stop distance (e.g., 1.5% or 500):",
            "partial_close_custom": "💰 Send the custom partial close Percentage (e.g., 30):"
        }
        prompt_text = prompts.get(action, 'Send the new value:')
        await safe_edit_message(
             context.bot, chat_id, message_id,
            text=f"⚠️ **Invalid Input:** {e}\n\n<b>{prompt_text}</b>",
            reply_markup=InlineKeyboardMarkup([[cancel_button]])
        )

    except Exception as e:
        loge.error(f"Error processing reply for {action} on {item_type} #{item_id}: {e}", exc_info=True)
        await context.bot.send_message(
             chat_id=chat_id,
             text=f"❌ Unexpected error processing input: {e}\nOperation cancelled."
        )
        clean_management_state(context)
        if item_id:
            await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)


@uow_transaction
@require_active_user
async def confirm_change_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer("Processing...")
    if await handle_management_timeout(update, context): return

    pending_data = context.user_data.pop(PENDING_CHANGE_KEY, None)
    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get('params', [])
    item_id = None
    item_type = 'rec'
    try:
        if len(params) >= 3:
             namespace, action, item_id_str = params[0], params[1], params[2]
             item_id = int(item_id_str)
             item_type = 'rec' if namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value] else 'trade'
        else: raise ValueError("Invalid confirmation callback format")

        if not pending_data or "value" not in pending_data:
            raise ValueError("No pending change found or data corrupt.")

        is_analyst_action = namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value]
        if is_analyst_action and (not db_user or db_user.user_type != UserTypeEntity.ANALYST):
             raise ValueError("Permission Denied: Analyst role required.")

        pending_value = pending_data["value"]
        trade_service = get_service(context, "trade_service", TradeService)
        user_telegram_id = str(db_user.telegram_user_id)
        success = False

        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            mode = pending_value["mode"]
            price = pending_value.get("price")
            trailing = pending_value.get("trailing_value")
            await trade_service.set_exit_strategy_async(item_id, user_telegram_id, mode, price=price, trailing_value=trailing, active=True, session=db_session)
            success = True
        elif namespace == CallbackNamespace.RECOMMENDATION.value:
            if action == "edit_sl": await trade_service.update_sl_for_user_async(item_id, user_telegram_id, pending_value, db_session); success = True
            elif action == "edit_entry": await trade_service.update_entry_and_notes_async(item_id, user_telegram_id, new_entry=pending_value, new_notes=None, db_session=db_session); success = True
            elif action == "close_manual": await trade_service.close_recommendation_async(item_id, user_telegram_id, pending_value, db_session, reason="MANUAL_PRICE_CLOSE"); success = True
            elif action == "edit_tp": await trade_service.update_targets_for_user_async(item_id, user_telegram_id, pending_value, db_session); success = True
            elif action == "edit_notes": await trade_service.update_entry_and_notes_async(item_id, user_telegram_id, new_entry=None, new_notes=pending_value, db_session=db_session); success = True
            elif action == "partial_close_custom":
                 price_service = get_service(context, "price_service", PriceService)
                 rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, 'rec', item_id)
                 if not rec: raise ValueError("Recommendation not found.")
                 
                 live_price = await price_service.get_cached_price(_get_attr(rec.asset, 'value'), _get_attr(rec, 'market'), force_refresh=True)
                 if not live_price: raise ValueError(f"Could not fetch market price for {_get_attr(rec.asset, 'value')}.")
                 await trade_service.partial_close_async(item_id, user_telegram_id, pending_value, Decimal(str(live_price)), db_session, triggered_by="MANUAL_CUSTOM"); success = True

        if success:
             await query.answer("✅ Action Successful!")
             await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)

    except (ValueError, Exception) as e:
        loge.error(f"Error confirming change for {action} on {item_type} #{item_id}: {e}", exc_info=True)
        try: await query.answer(f"❌ Execution Failed: {str(e)[:150]}", show_alert=True)
        except TelegramError: pass
        if item_id: await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)
    finally:
        clean_management_state(context)

@uow_transaction
@require_active_user
async def cancel_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer("Input cancelled.")
    if await handle_management_timeout(update, context): return

    state = context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)

    item_id = None
    item_type = 'rec'
    if state:
        item_id = state.get("item_id")
        item_type = state.get("item_type", 'rec')
    elif query and query.data:
         params = CallbackBuilder.parse(query.data).get('params', [])
         if params and params[0].isdigit(): item_id = int(params[0])

    if item_id is not None:
         await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)
    elif query and query.message:
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Input cancelled.")


@uow_transaction
@require_active_user
async def cancel_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer("Action cancelled.")
    if await handle_management_timeout(update, context): return

    clean_management_state(context)

    item_id = None
    item_type = 'rec'
    if query and query.data:
         params = CallbackBuilder.parse(query.data).get('params', [])
         if params and params[0].isdigit(): item_id = int(params[0])

    if item_id is not None:
         await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)
    elif query and query.message:
         await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Action cancelled.")


@uow_transaction
@require_active_user
@require_analyst_user
async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer("Processing...")
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data)
    namespace = parsed_data.get('namespace')
    action = parsed_data.get('action')
    params = parsed_data.get('params', [])
    rec_id = int(params[0]) if params and params[0].isdigit() else None

    if rec_id is None:
        loge.error(f"Could not get rec_id from immediate action callback: {query.data}")
        await query.answer("❌ Invalid request.", show_alert=True)
        return

    trade_service = get_service(context, "trade_service", TradeService)
    user_telegram_id = str(db_user.telegram_user_id)
    success_message = None
    item_type = 'rec'

    try:
        position = trade_service.get_position_details_for_user(db_session, user_telegram_id, item_type, rec_id)
        if not position: raise ValueError("Recommendation not found or closed.")
        if action != "cancel" and position.status != RecommendationStatus.ACTIVE:
             raise ValueError(f"Action '{action}' requires ACTIVE status (current: {position.status.value}).")

        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
             if action == "move_to_be":
                await trade_service.move_sl_to_breakeven_async(rec_id, db_session)
                success_message = "✅ SL moved to Break Even."
             elif action == "cancel":
                 if getattr(position, 'profit_stop_active', False):
                    await trade_service.set_exit_strategy_async(rec_id, user_telegram_id, "NONE", active=False, session=db_session)
                    success_message = "❌ Automated exit strategy cancelled."
                 else:
                     success_message = "ℹ️ No active exit strategy to cancel."

        elif namespace == CallbackNamespace.RECOMMENDATION.value:
             if action == "close_market":
                price_service = get_service(context, "price_service", PriceService)
                live_price = None
                try:
                    await query.answer("Fetching price...")
                    live_price = await price_service.get_cached_price(_get_attr(position.asset, 'value'), _get_attr(position, 'market'), force_refresh=True)
                    if not live_price: raise ValueError(f"Could not fetch market price for {_get_attr(position.asset, 'value')}.")
                except Exception as price_err:
                    loge.error(f"Failed to get live price for close_market #{rec_id}: {price_err}")
                    await query.answer(f"❌ Price Fetch Failed: {price_err}", show_alert=True)
                    return

                try:
                    await query.answer("Closing...")
                    await trade_service.close_recommendation_async(rec_id, user_telegram_id, Decimal(str(live_price)), db_session, reason="MARKET_CLOSE_MANUAL")
                    success_message = f"✅ Position closed at market price ~{_format_price(live_price)}."
                except Exception as close_err:
                    loge.error(f"Failed to close recommendation #{rec_id} via close_market: {close_err}", exc_info=True)
                    raise close_err

        if success_message: await query.answer(success_message)
        await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id)

    except (ValueError, Exception) as e:
        error_text = f"❌ Action Failed: {str(e)[:150]}"
        loge.error(f"Error in immediate action {namespace}:{action} for {item_type} #{rec_id}: {e}", exc_info=True)
        try: await query.answer(error_text, show_alert=True)
        except TelegramError: pass
        if rec_id: await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id)
    finally:
        context.user_data.pop(PENDING_CHANGE_KEY, None)


@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer("Processing...")
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get('params', [])
    rec_id, close_percent_str = None, None
    item_type = 'rec'
    try:
         if len(params) >= 2:
              rec_id = int(params[0])
              close_percent_str = params[1]
              close_percent = Decimal(close_percent_str)
              if not (0 < close_percent <= 100): raise ValueError("Invalid percentage")
         else: raise ValueError("Invalid callback format")
    except (ValueError, IndexError, TypeError) as e:
         loge.error(f"Could not parse partial close fixed callback: {query.data}, error: {e}")
         await query.answer("❌ Invalid request.", show_alert=True)
         return

    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    user_telegram_id = str(db_user.telegram_user_id)

    try:
        position = trade_service.get_position_details_for_user(db_session, user_telegram_id, item_type, rec_id)
        if not position: raise ValueError("Recommendation not found.")
        if position.status != RecommendationStatus.ACTIVE: raise ValueError("Can only partially close ACTIVE positions.")

        live_price = None
        try:
            await query.answer("Fetching price...")
            live_price = await price_service.get_cached_price(_get_attr(position.asset, 'value'), _get_attr(position, 'market'), force_refresh=True)
            if not live_price: raise ValueError(f"Could not fetch market price for {_get_attr(position.asset, 'value')}.")
        except Exception as price_err:
            loge.error(f"Failed to get live price for partial_close_fixed #{rec_id}: {price_err}")
            await query.answer(f"❌ Price Fetch Failed: {price_err}", show_alert=True)
            return

        await trade_service.partial_close_async(
             rec_id, user_telegram_id, close_percent, Decimal(str(live_price)), db_session, triggered_by="MANUAL_FIXED"
        )
        await query.answer(f"✅ Closed {close_percent:g}% at market price ~{_format_price(live_price)}.")

        await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id)
    except (ValueError, Exception) as e:
        loge.error(f"Error in partial close fixed handler for rec #{rec_id}: {e}", exc_info=True)
        try: await query.answer(f"❌ Partial Close Failed: {str(e)[:150]}", show_alert=True)
        except TelegramError: pass
        await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id)
    finally:
         clean_management_state(context)


@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get('params', [])
    rec_id = int(params[0]) if params and params[0].isdigit() else None

    if rec_id is None:
        loge.error(f"Could not get rec_id for partial_close_custom_start: {query.data}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request.", reply_markup=None)
        return ConversationHandler.END

    context.user_data['partial_close_rec_id'] = rec_id
    context.user_data['original_message_chat_id'] = query.message.chat_id
    context.user_data['original_message_message_id'] = query.message.message_id
    
    cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id))
    await safe_edit_message(
        context.bot, query.message.chat_id, query.message.message_id,
        text=f"{query.message.text_html}\n\n<b>💰 Send the custom Percentage to close (e.g., 30):</b>",
        reply_markup=InlineKeyboardMarkup([[cancel_button]])
    )
    return AWAIT_PARTIAL_PERCENT

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)

    rec_id = context.user_data.get('partial_close_rec_id')
    chat_id = context.user_data.get('original_message_chat_id')
    message_id = context.user_data.get('original_message_message_id')
    user_input = update.message.text.strip() if update.message.text else ""
    
    try: await update.message.delete()
    except Exception: log.debug("Could not delete user reply")
    
    if not (rec_id and chat_id and message_id):
        loge.error(f"Partial close percent handler for user {update.effective_user.id} has corrupt state.")
        clean_management_state(context)
        return ConversationHandler.END
        
    try:
        percent_val = parse_number(user_input.replace('%',''))
        if percent_val is None or not (0 < percent_val <= Decimal('100')):
            raise ValueError("Percentage must be between 0 and 100.")
        
        context.user_data['partial_close_percent'] = percent_val
        cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id))
        await safe_edit_message(
            context.bot, chat_id, message_id,
            text=f"✅ Closing {percent_val:g}%.\n\n<b>✍️ Send the custom Exit Price:</b>\n(or send '<b>market</b>' to use live price)",
            reply_markup=InlineKeyboardMarkup([[cancel_button]])
        )
        return AWAIT_PARTIAL_PRICE
        
    except (ValueError, Exception) as e:
        cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id))
        await safe_edit_message(
            context.bot, chat_id, message_id,
            text=f"⚠️ **Invalid Percentage:** {e}\n\n<b>💰 Send Percentage to close (e.g., 30):</b>",
            reply_markup=InlineKeyboardMarkup([[cancel_button]])
        )
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    if await handle_management_timeout(update, context): return ConversationHandler.END
    
    rec_id = context.user_data.get('partial_close_rec_id')
    percent_val = context.user_data.get('partial_close_percent')
    chat_id = context.user_data.get('original_message_chat_id')
    message_id = context.user_data.get('original_message_message_id')
    user_input = update.message.text.strip() if update.message.text else ""

    try: await update.message.delete()
    except Exception: log.debug("Could not delete user reply")

    if not (rec_id and percent_val and chat_id and message_id):
        loge.error(f"Partial close price handler for user {update.effective_user.id} has corrupt state.")
        clean_management_state(context)
        return ConversationHandler.END

    trade_service = get_service(context, "trade_service", TradeService)
    user_telegram_id = str(db_user.telegram_user_id)
    exit_price: Optional[Decimal] = None

    try:
        if user_input.lower() == 'market':
            price_service = get_service(context, "price_service", PriceService)
            position = trade_service.get_position_details_for_user(db_session, user_telegram_id, 'rec', rec_id)
            if not position: raise ValueError("Recommendation not found.")
            
            live_price = await price_service.get_cached_price(_get_attr(position.asset, 'value'), _get_attr(position, 'market'), force_refresh=True)
            if not live_price: raise ValueError(f"Could not fetch market price for {_get_attr(position.asset, 'value')}.")
            exit_price = Decimal(str(live_price))
        else:
            price_val = parse_number(user_input)
            if price_val is None:
                raise ValueError("Invalid price format. Send a number or 'market'.")
            exit_price = price_val
            
        await trade_service.partial_close_async(
            rec_id, user_telegram_id, percent_val, exit_price, db_session, triggered_by="MANUAL_CUSTOM"
        )
        
        query = update.callback_query or (update.effective_message and update.effective_message.reply_to_message and update.effective_message.reply_to_message.callback_query)
        if query:
            await query.answer(f"✅ Closed {percent_val:g}% at {_format_price(exit_price)}.")
        
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)

    except (ValueError, Exception) as e:
        loge.error(f"Error in custom partial close execution for rec #{rec_id}: {e}", exc_info=True)
        cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id))
        await safe_edit_message(
            context.bot, chat_id, message_id,
            text=f"⚠️ **Error:** {e}\n\n<b>✍️ Send the custom Exit Price:</b>\n(or send '<b>market</b>' to use live price)",
            reply_markup=InlineKeyboardMarkup([[cancel_button]])
        )
        return AWAIT_PARTIAL_PRICE

    clean_management_state(context)
    return ConversationHandler.END

async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = context.user_data.get('original_message_chat_id')
    message_id = context.user_data.get('original_message_message_id')
    
    clean_management_state(context)
    
    if update.callback_query: await update.callback_query.answer("Cancelled")
    
    if chat_id and message_id:
        await safe_edit_message(context.bot, chat_id, message_id, text="❌ Partial close cancelled.", reply_markup=None)
    elif update.message:
        await update.message.reply_text("❌ Partial close cancelled.", reply_markup=ReplyKeyboardRemove())
        
    return ConversationHandler.END
    

@uow_transaction
@require_active_user
async def user_trade_close_start(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)
    
    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get('params', [])
    
    trade_id = int(params[1]) if params and len(params) > 1 and params[1].isdigit() else None
    
    if trade_id is None:
        loge.error(f"Could not get trade_id for user_trade_close_start: {query.data}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request.", reply_markup=None)
        return ConversationHandler.END
        
    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), 'trade', trade_id)
    
    if not position or position.status != RecommendationStatus.ACTIVE:
        await query.answer("❌ Trade not found or is already closed.", show_alert=True)
        if position:
             await _send_or_edit_position_panel(update, context, db_session, 'trade', trade_id)
        return ConversationHandler.END
        
    context.user_data['user_trade_close_id'] = trade_id
    context.user_data['user_trade_close_chat_id'] = query.message.chat_id
    context.user_data['user_trade_close_msg_id'] = query.message.message_id
    
    cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", trade_id))
    await safe_edit_message(
        context.bot, query.message.chat_id, query.message.message_id,
        text=f"{query.message.text_html}\n\n<b>✍️ Send the final Exit Price for {_get_attr(position.asset, 'value')}:</b>",
        reply_markup=InlineKeyboardMarkup([[cancel_button]])
    )
    return AWAIT_USER_TRADE_CLOSE_PRICE

@uow_transaction
@require_active_user
async def user_trade_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    if await handle_management_timeout(update, context): return ConversationHandler.END
    
    trade_id = context.user_data.get('user_trade_close_id')
    chat_id = context.user_data.get('user_trade_close_chat_id')
    message_id = context.user_data.get('user_trade_close_msg_id')
    user_input = update.message.text.strip() if update.message.text else ""

    try: await update.message.delete()
    except Exception: log.debug("Could not delete user reply")

    if not (trade_id and chat_id and message_id):
        loge.error(f"User trade close handler for user {update.effective_user.id} has corrupt state.")
        clean_management_state(context)
        return ConversationHandler.END
        
    trade_service = get_service(context, "trade_service", TradeService)
    user_telegram_id = str(db_user.telegram_user_id)
    
    try:
        exit_price = parse_number(user_input)
        if exit_price is None:
            raise ValueError("Invalid price format. Send a valid number.")
            
        closed_trade = await trade_service.close_user_trade_async(
            user_telegram_id, trade_id, exit_price, db_session
        )
        
        if not closed_trade:
             raise ValueError("Trade not found or access denied.")
             
        pnl_pct = closed_trade.pnl_percentage
        pnl_str = f"({pnl_pct:+.2f}%)" if pnl_pct is not None else ""
        
        await safe_edit_message(
            context.bot, chat_id, message_id,
            text=f"✅ <b>Trade Closed</b>\n{closed_trade.asset} closed at {_format_price(exit_price)} {pnl_str}.",
            reply_markup=None
        )

    except (ValueError, Exception) as e:
        loge.error(f"Error in user trade close execution for trade #{trade_id}: {e}", exc_info=True)
        cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", trade_id))
        await safe_edit_message(
            context.bot, chat_id, message_id,
            text=f"⚠️ **Error:** {e}\n\n<b>✍️ Send the final Exit Price:</b>",
            reply_markup=InlineKeyboardMarkup([[cancel_button]])
        )
        return AWAIT_USER_TRADE_CLOSE_PRICE

    clean_management_state(context)
    return ConversationHandler.END

async def cancel_user_trade_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    trade_id = context.user_data.get('user_trade_close_id')
    chat_id = context.user_data.get('user_trade_close_chat_id')
    message_id = context.user_data.get('original_message_message_id')
    
    clean_management_state(context)
    
    if update.callback_query: await update.callback_query.answer("Cancelled")
    
    if chat_id and message_id:
        await safe_edit_message(context.bot, chat_id, message_id, text="❌ Close operation cancelled.", reply_markup=None)
    elif update.message:
        await update.message.reply_text("❌ Close operation cancelled.", reply_markup=ReplyKeyboardRemove())
        
    return ConversationHandler.END
    

# --- Handler Registration ---
def register_management_handlers(app: Application):
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))

    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{CallbackNamespace.NAVIGATION.value}:{CallbackAction.NAVIGATE.value}:"), group=1)
    
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"), group=1)
    
    app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_menu|close_menu|partial_close_menu|show_menu):"), group=1)
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_|set_|close_manual|partial_close_custom)"), group=1)
    app.add_handler(CallbackQueryHandler(confirm_change_handler, pattern=rf"^mgmt:confirm_change:"), group=1)
    app.add_handler(CallbackQueryHandler(cancel_input_handler, pattern=rf"^mgmt:cancel_input:"), group=1)
    app.add_handler(CallbackQueryHandler(cancel_all_handler, pattern=rf"^mgmt:cancel_all:"), group=1)
    app.add_handler(CallbackQueryHandler(immediate_action_handler, pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}:(?:move_to_be|cancel):|{CallbackNamespace.RECOMMENDATION.value}:close_market)"), group=1)
    app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:{CallbackAction.PARTIAL.value}:\d+:(?:25|50)$"), group=1)

    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, reply_handler), group=0)

    partial_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_close_custom_start, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:partial_close_custom:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_percent_received)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", partial_close_cancel),
            CallbackQueryHandler(partial_close_cancel, pattern=rf"^mgmt:cancel_input:")
        ],
        name="partial_close_conversation",
        per_user=True, per_chat=True, conversation_timeout=MANAGEMENT_TIMEOUT, persistent=False,
        per_message=False
    )
    app.add_handler(partial_close_conv, group=0)

    user_trade_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(user_trade_close_start, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.CLOSE.value}:trade:")],
        states={
            AWAIT_USER_TRADE_CLOSE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, user_trade_close_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_user_trade_close),
            CallbackQueryHandler(cancel_user_trade_close, pattern=rf"^mgmt:cancel_input:")
        ],
        name="user_trade_close_conversation",
        per_user=True, per_chat=True, conversation_timeout=MANAGEMENT_TIMEOUT, persistent=False,
        per_message=False
    )
    app.add_handler(user_trade_close_conv, group=0)
--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---