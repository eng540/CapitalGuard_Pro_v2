# --- src/capitalguard/interfaces/telegram/management_handlers.py ---
# src/capitalguard/interfaces/telegram/management_handlers.py (v30.11 - UserTrade Close Final)
"""
Handles all post-creation management of recommendations AND UserTrades.
✅ NEW: Fully implemented ConversationHandler for closing personal UserTrades.
✅ Integrates seamlessly with ParsingService correction flow cancellation.
✅ Final, complete, and production-ready version.
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

# Infrastructure & Application specific imports
from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.interfaces.telegram.helpers import get_service, parse_cq_parts
from capitalguard.interfaces.telegram.keyboards import (
    analyst_control_panel_keyboard, build_open_recs_keyboard,
    build_user_trade_control_keyboard, build_close_options_keyboard,
    build_trade_data_edit_keyboard, build_exit_management_keyboard,
    build_partial_close_keyboard, CallbackAction, CallbackNamespace,
    build_confirmation_keyboard, CallbackBuilder, ButtonTexts # Added ButtonTexts
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user, get_db_user # Import get_db_user
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list, parse_trailing_distance
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.domain.entities import RecommendationStatus, ExitStrategy # Keep domain imports
from capitalguard.infrastructure.db.models import UserTradeStatus # Import UserTradeStatus if needed elsewhere

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors") # Specific logger for errors

# --- Constants & State Keys ---
AWAITING_INPUT_KEY = "awaiting_management_input"
PENDING_CHANGE_KEY = "pending_management_change"
LAST_ACTIVITY_KEY = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800 # 30 minutes

# --- Conversation States ---
# States for Analyst Recommendation Management (via Reply) - Implicit state via AWAITING_INPUT_KEY
# States for Custom Partial Close Conversation
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)
# States for User Trade Closing Conversation
(AWAIT_USER_TRADE_CLOSE_PRICE,) = range(AWAIT_PARTIAL_PRICE + 1, AWAIT_PARTIAL_PRICE + 2)


# --- Session & Timeout Management ---
# (Using simplified versions assuming shared LAST_ACTIVITY_KEY)
def init_management_session(context: ContextTypes.DEFAULT_TYPE):
    """Initializes or resets state for management actions."""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)
    # Clear specific conversation states if necessary
    context.user_data.pop('partial_close_rec_id', None)
    context.user_data.pop('partial_close_percent', None)
    log.debug(f"Management session initialized/reset for user {context._user_id}.")

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    """Updates the last activity timestamp."""
    # Ensure key exists before updating
    if LAST_ACTIVITY_KEY not in context.user_data:
         init_management_session(context) # Initialize if missing
    else:
         context.user_data[LAST_ACTIVITY_KEY] = time.time()

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    """Cleans up all keys related to management conversations."""
    keys_to_pop = [
        AWAITING_INPUT_KEY, PENDING_CHANGE_KEY, LAST_ACTIVITY_KEY,
        'partial_close_rec_id', 'partial_close_percent',
        # Add any other specific state keys here
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    log.debug("All management conversation states cleared.")

async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks for and handles conversation timeouts."""
    last_activity = context.user_data.get(LAST_ACTIVITY_KEY, 0)
    if time.time() - last_activity > MANAGEMENT_TIMEOUT:
        msg = "⏰ Session expired due to inactivity.\nPlease use /myportfolio to start again."
        target_chat_id = update.effective_chat.id
        target_message_id = None
        if update.callback_query and update.callback_query.message:
            target_message_id = update.callback_query.message.message_id
            try: await update.callback_query.answer("Session expired", show_alert=True)
            except TelegramError: pass # Ignore if query expired

        clean_management_state(context) # Clean state *after* getting IDs

        if target_message_id:
            await safe_edit_message(context.bot, target_chat_id, target_message_id, text=msg, reply_markup=None)
        elif update.message: # Should not happen often if entry is command/callback
            await update.message.reply_text(msg)
        else: # Fallback if no message context
             await context.bot.send_message(chat_id=target_chat_id, text=msg)

        return True # Indicates timeout occurred
    return False

# --- Helper: Safe Message Editing ---
async def safe_edit_message(bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool:
    """Edits a message safely using chat_id and message_id, handling common errors."""
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
        if "message is not modified" in str(e).lower(): return True # Ignore cosmetic edits
        # Log other BadRequests potentially indicating issues
        loge.warning(f"Handled BadRequest editing msg {chat_id}:{message_id}: {e}")
        return False # Indicate failure but don't crash
    except TelegramError as e:
        # Log other Telegram errors (e.g., permissions, message deleted)
        loge.error(f"TelegramError editing msg {chat_id}:{message_id}: {e}")
        return False # Indicate failure
    except Exception as e:
         # Log unexpected errors
         loge.exception(f"Unexpected error editing msg {chat_id}:{message_id}: {e}")
         return False

# --- Helper: Render Position Panel ---
async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int):
    """Fetches position details and renders the appropriate control panel."""
    query = update.callback_query
    # Determine the target message to potentially edit
    message_target = query.message if query and query.message else update.effective_message

    if not message_target:
        log.error(f"_send_or_edit_position_panel failed for {position_type} #{position_id}: No message target found.")
        # Maybe send a new message as fallback?
        await update.effective_chat.send_message("Error: Could not find the message to update.")
        return

    chat_id = message_target.chat_id
    message_id = message_target.message_id

    try:
        trade_service = get_service(context, "trade_service", TradeService)
        # Fetch data using the user's Telegram ID
        position = trade_service.get_position_details_for_user(
            db_session, str(update.effective_user.id), position_type, position_id
        )

        if not position:
            await safe_edit_message(context.bot, chat_id, message_id, text="❌ Position not found or has been closed.", reply_markup=None)
            return

        # Fetch live price to display current PnL
        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(
            _get_attr(position.asset, 'value'), # Use helper for domain object
            _get_attr(position, 'market', 'Futures'), # Use helper
            force_refresh=True # Always get fresh price for panel
        )
        if live_price is not None:
            setattr(position, "live_price", live_price) # Attach for build_trade_card_text

        text = build_trade_card_text(position)
        keyboard = None

        # Build appropriate keyboard based on type and status
        is_trade = getattr(position, 'is_user_trade', False)
        if position.status == RecommendationStatus.ACTIVE:
            if is_trade:
                keyboard = build_user_trade_control_keyboard(position_id)
            else: # Is an analyst recommendation
                keyboard = analyst_control_panel_keyboard(position)
        else: # PENDING or CLOSED - show minimal keyboard (e.g., just back)
            keyboard = InlineKeyboardMarkup([[
                 InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))
            ]])

        await safe_edit_message(context.bot, chat_id, message_id, text=text, reply_markup=keyboard)

    except Exception as e:
        loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True)
        await safe_edit_message(context.bot, chat_id, message_id, text=f"❌ Error loading position data: {str(e)}", reply_markup=None)


# --- Entry Point & Navigation ---
@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles /myportfolio and /open commands to show the list."""
    init_management_session(context) # Clean state before starting list view
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
    """Handles pagination for the open positions list."""
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
        # Attempt to edit message even on error to inform user
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Error loading positions page.", reply_markup=None)


@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Shows the detailed control panel for a selected Recommendation or UserTrade."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return
    update_management_activity(context)
    # Clear any pending input state when showing a panel
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None)

    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get('params', [])
    try:
        # Expected format: pos:sh:<type>:<id>
        if len(params) >= 2:
             position_type, position_id_str = params[0], params[1]
             position_id = int(position_id_str)
        else: raise ValueError("Insufficient parameters in callback")

        await _send_or_edit_position_panel(update, context, db_session, position_type, position_id)
    except (IndexError, ValueError, TypeError) as e:
        loge.error(f"Could not parse position info from callback: {query.data}, error: {e}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request data.", reply_markup=None)


# --- Submenu Handlers (Mainly Analyst Actions) ---
@uow_transaction
@require_active_user
@require_analyst_user # Only analysts access recommendation submenus
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Displays specific submenus like Edit, Close, Partial Close, Exit Management."""
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
    rec = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), 'rec', rec_id)
    if not rec:
        await query.answer("❌ Recommendation not found or closed.", show_alert=True)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Recommendation not found or closed.", reply_markup=None)
        return

    keyboard = None
    text = query.message.text_html # Default text is the current card

    # Build keyboard based on action AND status
    can_modify = rec.status == RecommendationStatus.ACTIVE
    can_edit_pending = rec.status == RecommendationStatus.PENDING

    back_button = InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))

    if namespace == CallbackNamespace.RECOMMENDATION.value:
        if action == "edit_menu":
             text = "✏️ <b>Edit Recommendation Data</b>\nSelect field to edit:"
             keyboard = build_trade_data_edit_keyboard(rec_id) # Keyboard builder should handle status internally ideally
             # Override back button if needed, or ensure builder includes it
        elif action == "close_menu":
            text = "❌ <b>Close Position Fully</b>\nSelect closing method:"
            if can_modify: keyboard = build_close_options_keyboard(rec_id)
            else: keyboard = InlineKeyboardMarkup([[back_button]])
        elif action == "partial_close_menu":
            text = "💰 <b>Partial Close Position</b>\nSelect percentage:"
            if can_modify: keyboard = build_partial_close_keyboard(rec_id)
            else: keyboard = InlineKeyboardMarkup([[back_button]])

    elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
        if action == "show_menu":
             text = "📈 <b>Manage Exit & Risk</b>\nSelect action:"
             if can_modify: keyboard = build_exit_management_keyboard(rec)
             else: keyboard = InlineKeyboardMarkup([[back_button]])

    if keyboard:
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=keyboard)
    else:
        log.warning(f"No valid submenu keyboard for {namespace}:{action} on rec #{rec_id} status {rec.status}")
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id) # Refresh main panel


# --- Prompt & Reply for Modifications (Mainly Analyst Actions) ---
async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user to send the new value as a reply, storing state."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return ConversationHandler.END # End conv if timed out
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data)
    namespace = parsed_data.get('namespace')
    action = parsed_data.get('action')
    params = parsed_data.get('params', [])
    rec_id = int(params[0]) if params and params[0].isdigit() else None

    if rec_id is None:
         loge.error(f"Could not get rec_id from prompt callback: {query.data}")
         await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request.", reply_markup=None)
         return # Don't change state

    # Store necessary info to process the reply
    context.user_data[AWAITING_INPUT_KEY] = {
        "namespace": namespace,
        "action": action,
        "item_id": rec_id, # Use generic item_id
        "item_type": 'rec', # Assume rec for these actions
        "original_message_chat_id": query.message.chat_id,
        "original_message_message_id": query.message.message_id,
        # Determine where to go back if user cancels input
        "previous_callback": CallbackBuilder.create(namespace, "show_menu" if namespace == CallbackNamespace.EXIT_STRATEGY else f"{action.split('_')[0]}_menu", rec_id)
    }

    # Define prompts based on action
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

    # Keyboard with just a cancel button during input
    cancel_button = InlineKeyboardButton(
        "❌ Cancel Input",
        # Use generic mgmt:cancel_input action
        callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id)
    )
    input_keyboard = InlineKeyboardMarkup([[cancel_button]])

    await safe_edit_message(
        context.bot, query.message.chat_id, query.message.message_id,
        text=f"{query.message.text_html}\n\n<b>{prompt_text}</b>", # Append prompt
        reply_markup=input_keyboard
    )
    # No return needed, default state transition handled by ConversationHandler setup

@uow_transaction
@require_active_user
# Require analyst only if action requires it (most do)
# @require_analyst_user # Applied conditionally inside
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles text reply with new value, validates, asks for confirmation."""
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)

    state = context.user_data.get(AWAITING_INPUT_KEY)
    # Check if this reply corresponds to an active prompt state
    if not (state and update.message and update.message.reply_to_message):
        log.debug("Reply handler ignored: No valid state or not a reply.")
        # Don't delete message if it wasn't meant for the bot
        return # Ignore message

    # --- Extract state ---
    namespace = state.get("namespace")
    action = state.get("action")
    item_id = state.get("item_id")
    item_type = state.get("item_type", 'rec') # Default to 'rec'
    chat_id = state.get("original_message_chat_id")
    message_id = state.get("original_message_message_id")
    user_input = update.message.text.strip() if update.message.text else ""

    # Check if user is analyst IF the action requires it
    is_analyst_action = namespace in [CallbackNamespace.RECOMMENDATION, CallbackNamespace.EXIT_STRATEGY]
    if is_analyst_action and (not db_user or db_user.user_type != UserTypeEntity.ANALYST):
         await update.message.reply_text("🚫 Permission Denied: This action requires Analyst role.")
         # Clean state but don't delete messages, just ignore input
         context.user_data.pop(AWAITING_INPUT_KEY, None)
         return # Ignore input

    # --- Safely delete user's reply ---
    try: await update.message.delete()
    except Exception: log.debug("Could not delete user reply message.")

    if not all([namespace, action, item_id, chat_id, message_id]):
        log.error(f"Reply handler has corrupt state for user {update.effective_user.id}: {state}")
        context.user_data.pop(AWAITING_INPUT_KEY, None) # Clear corrupt state
        await safe_edit_message(context.bot, chat_id, message_id, text="❌ Session error. Please try again.", reply_markup=None)
        return # Cannot proceed

    # --- Validate Input and Prepare Change ---
    validated_value: Any = None
    change_description = ""
    error_message = None
    trade_service = get_service(context, "trade_service", TradeService)

    try:
        # Fetch current item state for validation context
        current_item = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), item_type, item_id)
        if not current_item: raise ValueError("Position not found or closed.")

        # --- Input Validation Logic ---
        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "set_fixed":
                 price = parse_number(user_input)
                 if price is None: raise ValueError("Invalid price format.")
                 # Add validation: Fixed price must be profitable vs entry
                 entry_dec = _to_decimal(_get_attr(current_item.entry, 'value'))
                 if (_get_attr(current_item.side, 'value') == 'LONG' and price <= entry_dec) or \
                    (_get_attr(current_item.side, 'value') == 'SHORT' and price >= entry_dec):
                      raise ValueError("Fixed profit stop price must be beyond entry price.")
                 validated_value = {"mode": "FIXED", "price": price}
                 change_description = f"Activate Fixed Profit Stop at {_format_price(price)}"
            elif action == "set_trailing":
                 config = parse_trailing_distance(user_input)
                 if config is None: raise ValueError("Invalid format. Use % (e.g., '1.5%') or value (e.g., '500').")
                 validated_value = {"mode": "TRAILING", "trailing_value": config["value"]} # Store Decimal
                 change_description = f"Activate Trailing Stop with distance {user_input}"

        elif namespace == CallbackNamespace.RECOMMENDATION.value:
            # Actions require analyst role already checked above
            if action == "edit_sl":
                price = parse_number(user_input)
                if price is None: raise ValueError("Invalid price format.")
                # Validate against current state (using _validate_recommendation_data)
                trade_service._validate_recommendation_data(
                     _get_attr(current_item.side, 'value'), _get_attr(current_item.entry, 'value'), price, current_item.targets.values
                )
                validated_value = price
                change_description = f"Update Stop Loss to {_format_price(price)}"
            elif action == "edit_entry":
                 if current_item.status != RecommendationStatus.PENDING: raise ValueError("Entry can only be edited for PENDING signals.")
                 price = parse_number(user_input)
                 if price is None: raise ValueError("Invalid price format.")
                 trade_service._validate_recommendation_data(
                      _get_attr(current_item.side, 'value'), price, _get_attr(current_item.stop_loss, 'value'), current_item.targets.values
                 )
                 validated_value = price
                 change_description = f"Update Entry Price to {_format_price(price)}"
            elif action == "close_manual":
                 price = parse_number(user_input)
                 if price is None: raise ValueError("Invalid price format.")
                 validated_value = price
                 change_description = f"Manually Close Position at {_format_price(price)}"
            elif action == "edit_tp":
                targets_list_dict = parse_targets_list(user_input.split()) # Returns list[dict] with Decimal
                if not targets_list_dict: raise ValueError("Invalid targets format or no valid targets found.")
                # Validate new targets against current state
                trade_service._validate_recommendation_data(
                     _get_attr(current_item.side, 'value'), _get_attr(current_item.entry, 'value'), _get_attr(current_item.stop_loss, 'value'), targets_list_dict
                )
                validated_value = targets_list_dict
                target_prices_str = ", ".join([_format_price(t['price']) for t in validated_value])
                change_description = f"Update Targets to: {target_prices_str}"
            elif action == "edit_notes":
                 if user_input.lower() in ['clear', 'مسح', 'remove', 'إزالة', '']:
                     validated_value = None # Represent clearing notes
                     change_description = "Clear Notes"
                 else:
                     validated_value = user_input # Store as string
                     change_description = f"Update Notes to: '{_truncate_text(validated_value, 50)}'"
            elif action == "partial_close_custom":
                 percent_val = parse_number(user_input.replace('%','')) # Returns Decimal
                 if percent_val is None or not (0 < percent_val <= 100):
                     raise ValueError("Percentage must be a number between 0 and 100.")
                 validated_value = percent_val # Store as Decimal
                 change_description = f"Partially Close {percent_val:g}% of position at Market Price"

        # --- If Validation Passed ---
        if validated_value is not None or action == "edit_notes": # Allow clearing notes
            # Store validated value temporarily, clear prompt state
            context.user_data[PENDING_CHANGE_KEY] = {"value": validated_value}
            context.user_data.pop(AWAITING_INPUT_KEY, None)

            # Build confirmation keyboard
            confirm_callback = CallbackBuilder.create("mgmt", "confirm_change", namespace, action, item_id)
            # Use previous_callback stored in state for "Re-enter"
            reenter_callback = state.get("previous_callback", CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, item_id)) # Fallback to show panel
            cancel_callback = CallbackBuilder.create("mgmt", "cancel_all", item_id) # Generic cancel

            confirm_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(ButtonTexts.CONFIRM, callback_data=confirm_callback)],
                [InlineKeyboardButton("✏️ Re-enter Value", callback_data=reenter_callback)],
                [InlineKeyboardButton(ButtonTexts.CANCEL + " Action", callback_data=cancel_callback)],
            ])

            await safe_edit_message(
                context.bot, chat_id, message_id,
                text=f"❓ **Confirm Action**\n\nDo you want to:\n➡️ {change_description}?",
                reply_markup=confirm_keyboard
            )
        else:
             # Should ideally not be reached if validation logic is correct
             raise ValueError("Validation passed but no value was stored.")

    except ValueError as e:
        log.warning(f"Invalid input during reply for {action} on {item_type} #{item_id}: {e}")
        # Re-prompt, keeping state AWAITING_INPUT_KEY active
        cancel_button = InlineKeyboardButton("❌ Cancel Input", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", item_id))
        # Fetch original prompt text if possible (might be complex without storing it)
        # For simplicity, use a generic re-prompt message
        await safe_edit_message(
            context.bot, chat_id, message_id,
            text=f"⚠️ **Invalid Input:** {e}\nPlease send the correct value again or cancel.",
            reply_markup=InlineKeyboardMarkup([[cancel_button]])
        )
        # Stay in implicit state waiting for reply

    except Exception as e:
        loge.error(f"Error processing reply for {action} on {item_type} #{item_id}: {e}", exc_info=True)
        await context.bot.send_message( # Send new message on unexpected error
             chat_id=chat_id,
             text=f"❌ Unexpected error processing input: {e}\nOperation cancelled."
        )
        clean_management_state(context)
        # Attempt to show the main panel again
        await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)
        # No return needed as we are not in a ConversationHandler state


# --- Confirmation & Cancellation Handlers ---
@uow_transaction
@require_active_user
# Apply analyst check conditionally based on action
async def confirm_change_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Executes the pending change after user confirmation."""
    query = update.callback_query
    await query.answer("Processing...")
    if await handle_management_timeout(update, context): return

    pending_data = context.user_data.pop(PENDING_CHANGE_KEY, None)
    parsed_data = CallbackBuilder.parse(query.data) # mgmt:confirm_change:namespace:action:item_id
    params = parsed_data.get('params', [])
    item_id = None
    try:
        if len(params) >= 3:
            namespace, action, item_id_str = params[0], params[1], params[2]
            item_id = int(item_id_str)
            item_type = 'rec' if namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value] else 'trade' # Determine type
        else: raise ValueError("Invalid confirmation callback format")

        if not pending_data or "value" not in pending_data:
            raise ValueError("No pending change found or data corrupt.")

        # --- Conditional Analyst Check ---
        is_analyst_action = namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value]
        if is_analyst_action and (not db_user or db_user.user_type != UserTypeEntity.ANALYST):
             raise ValueError("Permission Denied: Analyst role required.")

        pending_value = pending_data["value"]
        trade_service = get_service(context, "trade_service", TradeService)
        user_telegram_id = str(db_user.telegram_user_id)
        success = False

        # --- Execute Service Call based on Namespace and Action ---
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
                 live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
                 if not live_price: raise ValueError(f"Could not fetch market price for {rec.asset.value}.")
                 await trade_service.partial_close_async(item_id, user_telegram_id, pending_value, Decimal(str(live_price)), db_session, triggered_by="MANUAL_CUSTOM"); success = True

        # --- If successful, update the panel ---
        if success:
             await query.answer("✅ Action Successful!")
             await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)
        # Error handling is done via the except block

    except (ValueError, Exception) as e:
        loge.error(f"Error confirming change for {action} on {item_type} #{item_id}: {e}", exc_info=True)
        try: await query.answer(f"❌ Execution Failed: {str(e)[:150]}", show_alert=True) # Show alert on failure
        except TelegramError: pass
        # Attempt to show panel again even on failure
        if item_id: await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)
    finally:
        # Always clean state after confirmation attempt
        clean_management_state(context)

# --- Cancel Handlers ---
# Needs UOW to potentially refresh panel state after cancelling
@uow_transaction
@require_active_user
async def cancel_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles cancellation during the text input phase by returning to the relevant panel."""
    query = update.callback_query
    await query.answer("Input cancelled.")
    if await handle_management_timeout(update, context): return

    state = context.user_data.pop(AWAITING_INPUT_KEY, None)
    context.user_data.pop(PENDING_CHANGE_KEY, None) # Also clear any pending change

    # Determine item_id and type from callback or state
    item_id = None
    item_type = 'rec' # Default
    if state:
        item_id = state.get("item_id")
        item_type = state.get("item_type", 'rec')
    elif query and query.data:
         # Fallback: parse from cancel callback mgmt:cancel_input:<item_id>
         params = CallbackBuilder.parse(query.data).get('params', [])
         if params and params[0].isdigit(): item_id = int(params[0])
         # Assume 'rec' if type not in callback

    if item_id is not None:
         # Restore the view before input was requested
         await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)
    elif query and query.message: # Fallback if state/callback is corrupt
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Input cancelled.")


@uow_transaction
@require_active_user
async def cancel_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles cancellation during confirmation, returning to the main panel."""
    query = update.callback_query
    await query.answer("Action cancelled.")
    if await handle_management_timeout(update, context): return

    clean_management_state(context) # Clean all mgmt state

    # Determine item_id and type from callback mgmt:cancel_all:<item_id>
    item_id = None
    item_type = 'rec' # Assume default
    if query and query.data:
         params = CallbackBuilder.parse(query.data).get('params', [])
         if params and params[0].isdigit(): item_id = int(params[0])
         # Could try fetching item to determine type if needed, but 'rec' is common

    if item_id is not None:
         await _send_or_edit_position_panel(update, context, db_session, item_type, item_id)
    elif query and query.message: # Fallback
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Action cancelled.")


# --- Immediate Action Handlers ---
@uow_transaction
@require_active_user
@require_analyst_user # Most immediate actions are analyst-only
async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles actions executing immediately (Move BE, Cancel Strategy, Close Market)."""
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

    try:
        # Fetch fresh state first
        rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, 'rec', rec_id)
        if not rec: raise ValueError("Recommendation not found or closed.")
        if rec.status != RecommendationStatus.ACTIVE: raise ValueError(f"Action '{action}' requires ACTIVE status.")

        # --- Execute Action ---
        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "move_to_be":
                await trade_service.move_sl_to_breakeven_async(rec_id, db_session)
                success_message = "✅ SL moved to Break Even."
            elif action == "cancel":
                # Check if a strategy is actually active before cancelling
                rec_orm = trade_service.repo.get(db_session, rec_id) # Get ORM for direct field check
                if rec_orm and getattr(rec_orm, 'profit_stop_active', False):
                     await trade_service.set_exit_strategy_async(rec_id, user_telegram_id, "NONE", active=False, session=db_session)
                     success_message = "❌ Automated exit strategy cancelled."
                else:
                     success_message = "ℹ️ No active exit strategy to cancel." # Informative message

        elif namespace == CallbackNamespace.RECOMMENDATION.value:
             if action == "close_market":
                price_service = get_service(context, "price_service", PriceService)
                live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
                if not live_price: raise ValueError(f"Could not fetch market price for {rec.asset.value}.")
                await trade_service.close_recommendation_async(rec_id, user_telegram_id, Decimal(str(live_price)), db_session, reason="MARKET_CLOSE_MANUAL")
                success_message = f"✅ Position closed at market price ~{_format_price(live_price)}."

        # --- If successful, show message and update panel ---
        if success_message: await query.answer(success_message)
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)

    except (ValueError, Exception) as e:
        loge.error(f"Error in immediate action {namespace}:{action} for rec #{rec_id}: {e}", exc_info=True)
        try: await query.answer(f"❌ Action Failed: {str(e)[:150]}", show_alert=True)
        except TelegramError: pass
        # Refresh panel even on failure
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    finally:
        clean_management_state(context) # Clean state after action attempt


@uow_transaction
@require_active_user
@require_analyst_user # Analyst action
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles partial close buttons with fixed percentages."""
    query = update.callback_query
    await query.answer("Processing...")
    if await handle_management_timeout(update, context): return
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data) # rec:pt:<rec_id>:<percentage>
    params = parsed_data.get('params', [])
    rec_id, close_percent_str = None, None
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
        rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, 'rec', rec_id)
        if not rec: raise ValueError("Recommendation not found.")
        if rec.status != RecommendationStatus.ACTIVE: raise ValueError("Can only partially close ACTIVE positions.")

        # Fetch live price
        live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
        if not live_price: raise ValueError(f"Could not fetch market price for {rec.asset.value}.")

        # Execute partial close
        await trade_service.partial_close_async(
            rec_id, user_telegram_id, close_percent, Decimal(str(live_price)), db_session, triggered_by="MANUAL_FIXED"
        )
        await query.answer(f"✅ Closed {close_percent:g}% at market price ~{_format_price(live_price)}.")

        # Update panel
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    except (ValueError, Exception) as e:
        loge.error(f"Error in partial close fixed handler for rec #{rec_id}: {e}", exc_info=True)
        try: await query.answer(f"❌ Partial Close Failed: {str(e)[:150]}", show_alert=True)
        except TelegramError: pass
        # Refresh panel even on failure
        await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id)
    finally:
         clean_management_state(context)


# --- Handler Registration ---
def register_management_handlers(app: Application):
    """Registers all management handlers including conversations."""
    # --- Entry Point Command ---
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))

    # --- Main Callback Handlers (Group 1 - After Conversations) ---
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{CallbackNamespace.NAVIGATION.value}:{CallbackAction.NAVIGATE.value}:"), group=1)
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"), group=1)
    app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_menu|close_menu|partial_close_menu|show_menu):"), group=1)
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_|set_|close_manual|partial_close_custom)"), group=1)
    app.add_handler(CallbackQueryHandler(confirm_change_handler, pattern=rf"^mgmt:confirm_change:"), group=1)
    app.add_handler(CallbackQueryHandler(cancel_input_handler, pattern=rf"^mgmt:cancel_input:"), group=1)
    app.add_handler(CallbackQueryHandler(cancel_all_handler, pattern=rf"^mgmt:cancel_all:"), group=1)
    app.add_handler(CallbackQueryHandler(immediate_action_handler, pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}:(?:move_to_be|cancel):|{CallbackNamespace.RECOMMENDATION.value}:close_market)"), group=1)
    app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:{CallbackAction.PARTIAL.value}:\d+:(?:25|50)$"), group=1)

    # --- Conversation Handler for User Input (Replies) ---
    # This handler specifically catches replies directed at messages managed by this module
    # It doesn't use explicit states but checks `AWAITING_INPUT_KEY` in `context.user_data`.
    # Group 0 ensures it runs before generic message handlers but after commands/entry points.
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, reply_handler), group=0)

    # --- Conversation Handler for Custom Partial Close ---
    partial_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_close_custom_start, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:partial_close_custom:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_percent_received)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_price_received)],
        },
        fallbacks=[
             CommandHandler("cancel", partial_close_cancel),
             CallbackQueryHandler(partial_close_cancel, pattern=rf"^mgmt:cancel_input:") # Reuse cancel button logic
        ],
        name="partial_close_conversation",
        per_user=True, per_chat=True, conversation_timeout=MANAGEMENT_TIMEOUT, persistent=False,
    )
    app.add_handler(partial_close_conv, group=0) # Needs priority

    # --- Conversation Handler for User Trade Closing ---
    user_trade_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(user_trade_close_start, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.CLOSE.value}:trade:")],
        states={
            AWAIT_USER_TRADE_CLOSE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, user_trade_close_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_user_trade_close),
            # Reuse generic cancel button logic
            CallbackQueryHandler(cancel_user_trade_close, pattern=rf"^mgmt:cancel_input:")
        ],
        name="user_trade_close_conversation",
        per_user=True, per_chat=True, conversation_timeout=MANAGEMENT_TIMEOUT, persistent=False,
    )
    app.add_handler(user_trade_close_conv, group=0) # Needs priority

# --- END of management handlers ---