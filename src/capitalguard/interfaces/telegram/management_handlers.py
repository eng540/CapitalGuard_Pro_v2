# src/capitalguard/interfaces/telegram/management_handlers.py (v26.2 - COMPLETE, FINAL & PRODUCTION-READY)
"""
Implements all callback query handlers for managing existing recommendations and trades.

This module is responsible for the "view and edit" part of the user experience.
It handles displaying the control panel for a position and processing all
user interactions with that panel, such as initiating an edit or a close operation.
All logic is complete, final, and production-ready.
"""

import logging
import asyncio
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, MessageHandler, ContextTypes, filters)

from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_tail_int, parse_cq_parts
from .keyboards import (analyst_control_panel_keyboard, build_open_recs_keyboard, build_user_trade_control_keyboard, build_close_options_keyboard, analyst_edit_menu_keyboard)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

# Key for storing the state of a user waiting for text input.
AWAITING_INPUT_KEY = "awaiting_user_input_for"

# --- Main Panel Display Functions ---

async def _send_or_edit_position_panel(context: ContextTypes.DEFAULT_TYPE, db_session, chat_id: int, message_id: int, position_id: int, user_id: int, position_type: str):
    """
    Unified and robust function to render the management panel for any position type (rec or trade).
    It fetches fresh data, gets the live price, builds the text and keyboard, and edits the message.
    """
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        position = trade_service.get_position_details_for_user(db_session, str(user_id), position_type, position_id)
        if not position:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ **Error:** Position not found or access denied.")
            return

        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
        if live_price:
            setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)
        is_trade = getattr(position, 'is_user_trade', False)
        
        if is_trade:
            keyboard = build_user_trade_control_keyboard(position_id)
        else:
            keyboard = analyst_control_panel_keyboard(position) if position.status != RecommendationStatus.CLOSED else None

        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest as e:
        # Ignore "Message is not modified" errors, as they are expected if the data hasn't changed.
        if "Message is not modified" not in str(e):
            log.warning(f"Failed to edit panel message: {e}")
    except Exception as e:
        log.error(f"Critical error in _send_or_edit_position_panel for pos_id={position_id}: {e}", exc_info=True)
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ An unexpected error occurred while loading position details.")

# --- Callback Handlers for Navigation and Panel Display ---

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles callbacks to show the main management panel for a specific position."""
    query = update.callback_query
    await query.answer()
    parts = parse_cq_parts(query.data)
    if len(parts) < 4: return # Invalid callback data
    position_type, position_id = parts[2], int(parts[3])
    await _send_or_edit_position_panel(context, db_session, query.message.chat_id, query.message.message_id, position_id, query.from_user.id, position_type)

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles pagination for the list of open positions."""
    query = update.callback_query
    await query.answer()
    page = parse_tail_int(query.data) or 1
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    items = trade_service.get_open_positions_for_user(db_session, str(query.from_user.id))
    if not items:
        await query.edit_message_text(text="✅ You have no open positions.")
        return
    keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
    await query.edit_message_text(text="<b>📊 Your Open Positions</b>\nSelect one to manage:", reply_markup=keyboard, parse_mode=ParseMode.HTML)

# --- Handlers for User Input Flow ---

async def _prompt_for_input(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, action: str, prompt_text: str):
    """Helper function to ask the user for text input via a reply."""
    rec_id = parse_tail_int(query.data)
    if rec_id is None: return
    
    # Store the action and original message details, so the reply handler knows what to do.
    context.user_data[AWAITING_INPUT_KEY] = {"action": action, "rec_id": rec_id, "original_message": query.message}
    
    full_prompt = f"{query.message.text}\n\n<b>{prompt_text}</b>"
    await query.edit_message_text(full_prompt, parse_mode=ParseMode.HTML)

@uow_transaction
@require_active_user
@require_analyst_user
async def unified_management_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
    """A single handler for all management buttons to keep logic clean and centralized."""
    query = update.callback_query
    await query.answer()
    
    parts = parse_cq_parts(query.data)
    action, rec_id_str = parts[1], parts[2]
    rec_id = int(rec_id_str)
    
    if action == "edit_menu": await query.edit_message_reply_markup(reply_markup=analyst_edit_menu_keyboard(rec_id))
    elif action == "close_menu": await query.edit_message_reply_markup(reply_markup=build_close_options_keyboard(rec_id))
    elif action == "edit_sl": await _prompt_for_input(query, context, "edit_sl", f"✏️ Reply to this message with the new Stop Loss for #{rec_id}.")
    elif action == "edit_tp": await _prompt_for_input(query, context, "edit_tp", f"🎯 Reply with the new list of targets for #{rec_id} (e.g., 50k 52k@50).")
    elif action == "close_manual": await _prompt_for_input(query, context, "close_manual", f"✍️ Reply with the final closing price for #{rec_id}.")

@uow_transaction
async def unified_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """
    A single, robust handler for all text inputs that are replies to the bot's prompts for modification.
    """
    # Check if this reply corresponds to a pending action.
    if not (state := context.user_data.pop(AWAITING_INPUT_KEY, None)) or not (orig_msg := state.get("original_message")) or update.message.reply_to_message.message_id != orig_msg.message_id:
        return

    action, position_id = state["action"], state["rec_id"]
    user_input, chat_id, message_id, user_id = update.message.text.strip(), orig_msg.chat_id, orig_msg.message_id, str(update.effective_user.id)
    
    # Clean up the user's reply to keep the chat tidy.
    try: await update.message.delete()
    except Exception: pass

    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        if action == "close_manual":
            await trade_service.close_recommendation_async(position_id, user_id, Decimal(user_input), db_session=db_session)
        elif action == "edit_sl":
            await trade_service.update_sl_for_user_async(position_id, user_id, Decimal(user_input), db_session=db_session)
        elif action == "edit_tp":
            targets_list = parse_targets_list(user_input.split())
            if not targets_list: raise ValueError("Invalid targets format. Please provide at least one target.")
            new_targets_decimal = [{'price': Decimal(str(t['price'])), 'close_percent': t['close_percent']} for t in targets_list]
            await trade_service.update_targets_for_user_async(position_id, user_id, new_targets_decimal, db_session=db_session)
        
        # On success, the service methods already handle notifications, so no need to do anything else here.
        # The panel will be updated automatically via the notification flow.

    except (InvalidOperation, ValueError, RuntimeError) as e:
        # If input is invalid or a business rule is violated, inform the user and restore the state.
        await context.bot.send_message(chat_id=chat_id, text=f"❌ **Action Failed:** {e}\nPlease try again.", reply_to_message_id=message_id)
        context.user_data[AWAITING_INPUT_KEY] = state # Restore state to allow user to retry
    except Exception as e:
        # For unexpected errors, log it and inform the user. Don't restore state.
        log.error(f"Critical error processing user input for {action} on #{position_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ An unexpected internal error occurred: {e}", reply_to_message_id=message_id)

# --- Registration ---

def register_management_handlers(application: Application):
    """Registers all callback query and message handlers for managing positions."""
    application.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=r"^open_nav:page:"))
    application.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^(pos:show_panel:|rec:back_to_main:)"))
    
    # A single, powerful handler for all management buttons on the analyst panel.
    application.add_handler(CallbackQueryHandler(unified_management_handler, pattern=r"^rec:(edit_menu|close_menu|edit_sl|edit_tp|close_manual)"))
    
    # The handler for processing text replies to the bot's modification prompts.
    # It has a high priority (group=1) to catch replies before other general message handlers.
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler), group=1)```