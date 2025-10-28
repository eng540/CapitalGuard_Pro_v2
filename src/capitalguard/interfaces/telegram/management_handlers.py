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
[cite_start]from capitalguard.infrastructure.db.uow import uow_transaction # [cite: 406-411]
[cite_start]from capitalguard.interfaces.telegram.helpers import get_service, parse_cq_parts # [cite: 1698-1700]
from capitalguard.interfaces.telegram.keyboards import (
    [cite_start]analyst_control_panel_keyboard, build_open_recs_keyboard, # [cite: 1757, 1761-1769]
    [cite_start]build_user_trade_control_keyboard, build_close_options_keyboard, # [cite: 1773, 1770]
    [cite_start]build_trade_data_edit_keyboard, build_exit_management_keyboard, # [cite: 1783, 1759]
    [cite_start]build_partial_close_keyboard, CallbackAction, CallbackNamespace, # [cite: 1787]
    [cite_start]build_confirmation_keyboard, CallbackBuilder, ButtonTexts # [cite: 1771, 1752]
)
[cite_start]from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text # [cite: 899-911]
[cite_start]from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user, get_db_user # [cite: 603-614]
[cite_start]from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list, parse_trailing_distance # [cite: 863-876]
[cite_start]from capitalguard.application.services.trade_service import TradeService # [cite: 164-293]
[cite_start]from capitalguard.application.services.price_service import PriceService # [cite: 138-147]
[cite_start]from capitalguard.domain.entities import RecommendationStatus, ExitStrategy # [cite: 312-320]
[cite_start]from capitalguard.infrastructure.db.models import UserTradeStatus # [cite: 335-336]

log = logging.getLogger(__name__)
[cite_start]loge = logging.getLogger("capitalguard.errors") # Specific logger for errors [cite: 790]

# --- Constants & State Keys ---
[cite_start]AWAITING_INPUT_KEY = "awaiting_management_input" # [cite: 791]
[cite_start]PENDING_CHANGE_KEY = "pending_management_change" # [cite: 791]
[cite_start]LAST_ACTIVITY_KEY = "last_activity_management" # [cite: 791]
[cite_start]MANAGEMENT_TIMEOUT = 1800 # 30 minutes [cite: 791]

# --- Conversation States ---
# States for Analyst Recommendation Management (via Reply) - Implicit state via AWAITING_INPUT_KEY
# States for Custom Partial Close Conversation
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) [cite_start]= range(2) # [cite: 1819]
# States for User Trade Closing Conversation
(AWAIT_USER_TRADE_CLOSE_PRICE,) [cite_start]= range(AWAIT_PARTIAL_PRICE + 1, AWAIT_PARTIAL_PRICE + 2) # [cite: 791]


# --- Session & Timeout Management ---
[cite_start]def init_management_session(context: ContextTypes.DEFAULT_TYPE): # [cite: 791]
    """Initializes or resets state for management actions."""
    [cite_start]context.user_data[LAST_ACTIVITY_KEY] = time.time() # [cite: 791]
    [cite_start]context.user_data.pop(AWAITING_INPUT_KEY, None) # [cite: 791]
    [cite_start]context.user_data.pop(PENDING_CHANGE_KEY, None) # [cite: 791]
    # Clear specific conversation states if necessary
    [cite_start]context.user_data.pop('partial_close_rec_id', None) # [cite: 791]
    [cite_start]context.user_data.pop('partial_close_percent', None) # [cite: 791]
    [cite_start]log.debug(f"Management session initialized/reset for user {context._user_id}.") # [cite: 791]

[cite_start]def update_management_activity(context: ContextTypes.DEFAULT_TYPE): # [cite: 791]
    """Updates the last activity timestamp."""
    # Ensure key exists before updating
    [cite_start]if LAST_ACTIVITY_KEY not in context.user_data: # [cite: 791]
         [cite_start]init_management_session(context) # Initialize if missing [cite: 791]
    else:
         [cite_start]context.user_data[LAST_ACTIVITY_KEY] = time.time() # [cite: 791]

[cite_start]def clean_management_state(context: ContextTypes.DEFAULT_TYPE): # [cite: 791]
    """Cleans up all keys related to management conversations."""
    [cite_start]keys_to_pop = [ # [cite: 792]
        [cite_start]AWAITING_INPUT_KEY, PENDING_CHANGE_KEY, LAST_ACTIVITY_KEY, # [cite: 792]
        [cite_start]'partial_close_rec_id', 'partial_close_percent', # [cite: 792]
        # Add any other specific state keys here
    ]
    [cite_start]for key in keys_to_pop: # [cite: 792]
        [cite_start]context.user_data.pop(key, None) # [cite: 792]
    [cite_start]log.debug("All management conversation states cleared.") # [cite: 792]

[cite_start]async def handle_management_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool: # [cite: 792]
    """Checks for and handles conversation timeouts."""
    [cite_start]last_activity = context.user_data.get(LAST_ACTIVITY_KEY, 0) # [cite: 792]
    [cite_start]if time.time() - last_activity > MANAGEMENT_TIMEOUT: # [cite: 792]
        msg = "⏰ Session expired due to inactivity.\nPlease use /myportfolio to start again." [cite_start]# [cite: 792]
        [cite_start]target_chat_id = update.effective_chat.id # [cite: 792]
        [cite_start]target_message_id = None # [cite: 792]
        [cite_start]if update.callback_query and update.callback_query.message: # [cite: 792]
            [cite_start]target_message_id = update.callback_query.message.message_id # [cite: 792]
            [cite_start]try: await update.callback_query.answer("Session expired", show_alert=True) # [cite: 793]
            [cite_start]except TelegramError: pass # Ignore if query expired [cite: 793]

        [cite_start]clean_management_state(context) # Clean state *after* getting IDs [cite: 793]

        [cite_start]if target_message_id: # [cite: 793]
            [cite_start]await safe_edit_message(context.bot, target_chat_id, target_message_id, text=msg, reply_markup=None) # [cite: 793]
        [cite_start]elif update.message: # Should not happen often if entry is command/callback [cite: 793]
            [cite_start]await update.message.reply_text(msg) # [cite: 793]
        [cite_start]else: # Fallback if no message context [cite: 793]
             [cite_start]await context.bot.send_message(chat_id=target_chat_id, text=msg) # [cite: 793]

        [cite_start]return True # Indicates timeout occurred [cite: 793]
    [cite_start]return False # [cite: 793]

# --- Helper: Safe Message Editing ---
[cite_start]async def safe_edit_message(bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool: # [cite: 794]
    """Edits a message safely using chat_id and message_id, handling common errors."""
    [cite_start]if not chat_id or not message_id: # [cite: 794]
        [cite_start]log.warning("safe_edit_message called without valid chat_id or message_id.") # [cite: 794]
        [cite_start]return False # [cite: 794]
    [cite_start]try: # [cite: 794]
        [cite_start]if text is not None: # [cite: 794]
            [cite_start]await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True) # [cite: 795]
        [cite_start]elif reply_markup is not None: # [cite: 795]
            [cite_start]await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup) # [cite: 795]
        [cite_start]return True # [cite: 795]
    [cite_start]except BadRequest as e: # [cite: 795]
        [cite_start]if "message is not modified" in str(e).lower(): return True # Ignore cosmetic edits [cite: 795]
        # Log other BadRequests potentially indicating issues
        [cite_start]loge.warning(f"Handled BadRequest editing msg {chat_id}:{message_id}: {e}") # [cite: 795]
        [cite_start]return False # Indicate failure but don't crash [cite: 796]
    [cite_start]except TelegramError as e: # [cite: 796]
        # Log other Telegram errors (e.g., permissions, message deleted)
        [cite_start]loge.error(f"TelegramError editing msg {chat_id}:{message_id}: {e}") # [cite: 796]
        [cite_start]return False # Indicate failure [cite: 796]
    [cite_start]except Exception as e: # [cite: 796]
         # Log unexpected errors
         [cite_start]loge.exception(f"Unexpected error editing msg {chat_id}:{message_id}: {e}") # [cite: 796]
         [cite_start]return False # [cite: 796]

# --- Helper: Render Position Panel ---
[cite_start]async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int): # [cite: 796]
    """Fetches position details and renders the appropriate control panel."""
    [cite_start]query = update.callback_query # Prefer query for editing [cite: 796]
    # Determine the target message to potentially edit
    [cite_start]message_target = query.message if query and query.message else update.effective_message # [cite: 796]

    [cite_start]if not message_target: # [cite: 796]
        [cite_start]log.error(f"_send_or_edit_position_panel failed for {position_type} #{position_id}: No message target found.") # [cite: 797]
        # Maybe send a new message as fallback?
        [cite_start]await update.effective_chat.send_message("Error: Could not find the message to update.") # [cite: 797]
        [cite_start]return # [cite: 797]

    [cite_start]chat_id = message_target.chat_id # [cite: 797]
    [cite_start]message_id = message_target.message_id # [cite: 797]

    [cite_start]try: # [cite: 797]
        [cite_start]trade_service = get_service(context, "trade_service", TradeService) # [cite: 797]
        # Fetch data using the user's Telegram ID
        [cite_start]position = trade_service.get_position_details_for_user( # [cite: 797]
            db_session, str(update.effective_user.id), position_type, position_id
        )

        [cite_start]if not position: # [cite: 797]
            [cite_start]await safe_edit_message(context.bot, chat_id, message_id, text="❌ Position not found or has been closed.", reply_markup=None) # [cite: 803]
            [cite_start]return # [cite: 803]

        # Fetch live price to display current PnL
        [cite_start]price_service = get_service(context, "price_service", PriceService) # [cite: 803]
        [cite_start]live_price = await price_service.get_cached_price( # [cite: 803]
            [cite_start]_get_attr(position.asset, 'value'), # Use helper for domain object [cite: 803]
            [cite_start]_get_attr(position, 'market', 'Futures'), # Use helper [cite: 803]
            [cite_start]force_refresh=True # Always get fresh price for panel [cite: 803]
        )
        [cite_start]if live_price is not None: # [cite: 803]
            [cite_start]setattr(position, "live_price", live_price) # Attach for build_trade_card_text [cite: 803]

        [cite_start]text = build_trade_card_text(position) # [cite: 803]
        [cite_start]keyboard = None # [cite: 803]

        # Build appropriate keyboard based on type and status
        [cite_start]is_trade = getattr(position, 'is_user_trade', False) # [cite: 803]
        [cite_start]if position.status == RecommendationStatus.ACTIVE: # [cite: 803]
            [cite_start]if is_trade: # [cite: 803]
                [cite_start]keyboard = build_user_trade_control_keyboard(position_id) # [cite: 803]
            [cite_start]else: # Is an analyst recommendation [cite: 803]
                [cite_start]keyboard = analyst_control_panel_keyboard(position) # [cite: 799]
        [cite_start]else: # PENDING or CLOSED - show minimal keyboard (e.g., just back) [cite: 799]
            [cite_start]keyboard = InlineKeyboardMarkup([[ # [cite: 799]
                 [cite_start]InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1)) # [cite: 799]
            ]])

        [cite_start]await safe_edit_message(context.bot, chat_id, message_id, text=text, reply_markup=keyboard) # [cite: 799]

    [cite_start]except Exception as e: # [cite: 799]
        [cite_start]loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True) # [cite: 804]
        [cite_start]await safe_edit_message(context.bot, chat_id, message_id, text=f"❌ Error loading position data: {str(e)}", reply_markup=None) # [cite: 804]


# --- Entry Point & Navigation ---
[cite_start]@uow_transaction # [cite: 800]
[cite_start]@require_active_user # [cite: 800]
[cite_start]async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs): # [cite: 800]
    """Handles /myportfolio and /open commands to show the list."""
    [cite_start]init_management_session(context) # Clean state before starting list view [cite: 800]
    [cite_start]try: # [cite: 800]
        [cite_start]trade_service = get_service(context, "trade_service", TradeService) # [cite: 800]
        [cite_start]price_service = get_service(context, "price_service", PriceService) # [cite: 800]
        [cite_start]items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id)) # [cite: 800]
        [cite_start]if not items: # [cite: 800]
            [cite_start]await update.message.reply_text("✅ No open positions found.") # [cite: 805]
            [cite_start]return # [cite: 805]
        [cite_start]keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service) # [cite: 801]
        [cite_start]await update.message.reply_html("<b>📊 Open Positions</b>\nSelect a position to manage:", reply_markup=keyboard) # [cite: 801]
    [cite_start]except Exception as e: # [cite: 801]
        [cite_start]loge.error(f"Error in management entry point: {e}", exc_info=True) # [cite: 801]
        [cite_start]await update.message.reply_text("❌ Error loading open positions.") # [cite: 801]

[cite_start]@uow_transaction # [cite: 801]
[cite_start]@require_active_user # [cite: 801]
[cite_start]async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs): # [cite: 801]
    """Handles pagination for the open positions list."""
    [cite_start]query = update.callback_query # [cite: 802]
    [cite_start]await query.answer() # [cite: 802]
    [cite_start]if await handle_management_timeout(update, context): return # [cite: 802]
    [cite_start]update_management_activity(context) # [cite: 802]

    [cite_start]parts = CallbackBuilder.parse(query.data).get('params', []) # [cite: 802]
    [cite_start]page = int(parts[0]) if parts and parts[0].isdigit() else 1 # [cite: 802]

    [cite_start]try: # [cite: 802]
        [cite_start]trade_service = get_service(context, "trade_service", TradeService) # [cite: 802]
        [cite_start]price_service = get_service(context, "price_service", PriceService) # [cite: 802]
        [cite_start]items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id)) # [cite: 802]
        [cite_start]keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service) # [cite: 802]
        [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="<b>📊 Open Positions</b>\nSelect a position to manage:", reply_markup=keyboard) # [cite: 802]
    [cite_start]except Exception as e: # [cite: 803]
        [cite_start]loge.error(f"Error navigating open positions (page {page}): {e}", exc_info=True) # [cite: 803]
        # Attempt to edit message even on error to inform user
        [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Error loading positions page.", reply_markup=None) # [cite: 803]


[cite_start]@uow_transaction # [cite: 803]
[cite_start]@require_active_user # [cite: 803]
[cite_start]async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs): # [cite: 803]
    """Shows the detailed control panel for a selected position."""
    [cite_start]query = update.callback_query # [cite: 803]
    [cite_start]await query.answer() # [cite: 803]
    [cite_start]if await handle_management_timeout(update, context): return # [cite: 803]
    [cite_start]update_management_activity(context) # [cite: 803]
    # Clear any pending input state when showing a panel
    [cite_start]context.user_data.pop(AWAITING_INPUT_KEY, None) # [cite: 804]
    [cite_start]context.user_data.pop(PENDING_CHANGE_KEY, None) # [cite: 804]

    [cite_start]parsed_data = CallbackBuilder.parse(query.data) # [cite: 804]
    [cite_start]params = parsed_data.get('params', []) # [cite: 804]
    [cite_start]try: # [cite: 804]
        # Expected format: pos:sh:<type>:<id>
        [cite_start]if len(params) >= 2: # [cite: 804]
             [cite_start]position_type, position_id_str = params[0], params[1] # [cite: 804]
             [cite_start]position_id = int(position_id_str) # [cite: 804]
        [cite_start]else: raise ValueError("Insufficient parameters in callback") # [cite: 804]

        [cite_start]await _send_or_edit_position_panel(update, context, db_session, position_type, position_id) # [cite: 805]
    [cite_start]except (IndexError, ValueError, TypeError) as e: # [cite: 805]
        [cite_start]loge.error(f"Could not parse position info from callback: {query.data}, error: {e}") # [cite: 805]
        [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request data.", reply_markup=None) # [cite: 805]


# --- Submenu Handlers (Mainly Analyst Actions) ---
[cite_start]@uow_transaction # [cite: 805]
[cite_start]@require_active_user # [cite: 805]
[cite_start]@require_analyst_user # Only analysts access recommendation submenus # [cite: 805]
[cite_start]async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs): # [cite: 806]
    """Displays specific submenus like Edit, Close, Partial Close, Exit Management."""
    [cite_start]query = update.callback_query # [cite: 806]
    [cite_start]await query.answer() # [cite: 806]
    [cite_start]if await handle_management_timeout(update, context): return # [cite: 806]
    [cite_start]update_management_activity(context) # [cite: 806]

    [cite_start]parsed_data = CallbackBuilder.parse(query.data) # [cite: 806]
    [cite_start]namespace = parsed_data.get('namespace') # [cite: 806]
    [cite_start]action = parsed_data.get('action') # [cite: 806]
    [cite_start]params = parsed_data.get('params', []) # [cite: 806]
    [cite_start]rec_id = int(params[0]) if params and params[0].isdigit() else None # [cite: 806]

    [cite_start]if rec_id is None: # [cite: 806]
        [cite_start]loge.error(f"Could not get rec_id from submenu callback: {query.data}") # [cite: 806]
        [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request.", reply_markup=None) # [cite: 806]
        [cite_start]return # [cite: 806]

    [cite_start]trade_service = get_service(context, "trade_service", TradeService) # [cite: 806]
    # Fetch recommendation to check status *before* showing the menu
    [cite_start]rec = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), 'rec', rec_id) # [cite: 807]
    [cite_start]if not rec: # [cite: 807]
        [cite_start]await query.answer("❌ Recommendation not found or closed.", show_alert=True) # [cite: 807]
        [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Recommendation not found or closed.", reply_markup=None) # [cite: 807]
        [cite_start]return # [cite: 807]

    [cite_start]keyboard = None # [cite: 807]
    [cite_start]text = query.message.text_html # Default text is the current card [cite: 807]

    # Build keyboard based on action AND status
    [cite_start]can_modify = rec.status == RecommendationStatus.ACTIVE # [cite: 807]
    [cite_start]can_edit_pending = rec.status == RecommendationStatus.PENDING # [cite: 807]

    [cite_start]back_button = InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id)) # [cite: 807]

    [cite_start]if namespace == CallbackNamespace.RECOMMENDATION.value: # [cite: 807]
        [cite_start]if action == "edit_menu": # [cite: 807]
             [cite_start]text = "✏️ <b>Edit Recommendation Data</b>\nSelect field to edit:" # [cite: 807]
             # Build keyboard based on status
             [cite_start]if rec.status == RecommendationStatus.ACTIVE or rec.status == RecommendationStatus.PENDING: # [cite: 808]
                 [cite_start]keyboard = build_trade_data_edit_keyboard(rec_id) # TODO: Hide 'edit_entry' if ACTIVE [cite: 808]
             [cite_start]else: # CLOSED or other states [cite: 808]
                 [cite_start]keyboard = InlineKeyboardMarkup([[back_button]]) # [cite: 808]
                 [cite_start]text = f"✏️ <b>Edit Recommendation Data</b>\n Cannot edit a recommendation with status {rec.status.value}" # [cite: 809]

        [cite_start]elif action == "close_menu": # [cite: 809]
            [cite_start]text = "❌ <b>Close Position Fully</b>\nSelect closing method:" # [cite: 809]
            [cite_start]if can_modify: keyboard = build_close_options_keyboard(rec_id) # [cite: 809]
            [cite_start]else: # [cite: 809]
                 [cite_start]keyboard = InlineKeyboardMarkup([[back_button]]) # [cite: 810]
                 [cite_start]text = f"❌ <b>Close Position Fully</b>\n Cannot close a recommendation with status {rec.status.value}" # [cite: 810]

        [cite_start]elif action == "partial_close_menu": # [cite: 810]
            [cite_start]text = "💰 <b>Partial Close Position</b>\nSelect percentage:" # [cite: 810]
            [cite_start]if can_modify: # [cite: 810]
                 [cite_start]keyboard = build_partial_close_keyboard(rec_id) # [cite: 811]
            [cite_start]else: # [cite: 811]
                 [cite_start]keyboard = InlineKeyboardMarkup([[back_button]]) # [cite: 811]
                 [cite_start]text = f"💰 <b>Partial Close Position</b>\n Cannot partially close a recommendation with status {rec.status.value}" # [cite: 811]

    [cite_start]elif namespace == CallbackNamespace.EXIT_STRATEGY.value: # [cite: 811]
        [cite_start]if action == "show_menu": # [cite: 811]
             [cite_start]text = "📈 <b>Manage Exit & Risk</b>\nSelect action:" # [cite: 812]
             [cite_start]if can_modify: keyboard = build_exit_management_keyboard(rec) # [cite: 812]
             [cite_start]else: # [cite: 812]
                [cite_start]keyboard = InlineKeyboardMarkup([[back_button]]) # [cite: 812]
                [cite_start]text = f"📈 <b>Manage Exit & Risk</b>\n Cannot manage exit for recommendation with status {rec.status.value}" # [cite: 813]

    [cite_start]if keyboard: # [cite: 813]
        [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=keyboard) # [cite: 813]
    [cite_start]else: # [cite: 813]
        # If no valid keyboard was built (e.g., invalid action), refresh main panel
        [cite_start]log.warning(f"No valid submenu keyboard for action '{action}' on rec #{rec_id} with status {rec.status}") # [cite: 813]
        [cite_start]await _send_or_edit_position_panel(update, context, db_session, 'rec', rec_id) # Refresh main panel [cite: 813]


# --- Prompt & Reply for Modifications (Mainly Analyst Actions) ---
[cite_start]async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): # [cite: 813]
    """Asks the user to send the new value as a reply, storing state."""
    [cite_start]query = update.callback_query # [cite: 814]
    [cite_start]await query.answer() # [cite: 814]
    [cite_start]if await handle_management_timeout(update, context): return ConversationHandler.END # End conv if timed out [cite: 814]
    [cite_start]update_management_activity(context) # [cite: 814]

    [cite_start]parsed_data = CallbackBuilder.parse(query.data) # [cite: 814]
    [cite_start]namespace = parsed_data.get('namespace') # [cite: 814]
    [cite_start]action = parsed_data.get('action') # [cite: 814]
    [cite_start]params = parsed_data.get('params', []) # [cite: 814]
    [cite_start]rec_id = int(params[0]) if params and params[0].isdigit() else None # [cite: 814]

    [cite_start]if rec_id is None: # [cite: 814]
         [cite_start]loge.error(f"Could not get rec_id from prompt callback: {query.data}") # [cite: 814]
         [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request.", reply_markup=None) # [cite: 815]
         [cite_start]return # Don't change state [cite: 815]

    # Store necessary info to process the reply
    [cite_start]context.user_data[AWAITING_INPUT_KEY] = { # [cite: 815]
        [cite_start]"namespace": namespace, # [cite: 815]
        [cite_start]"action": action, # [cite: 815]
        [cite_start]"item_id": rec_id, # Use generic item_id [cite: 815]
        [cite_start]"item_type": 'rec', # Assume rec for these actions [cite: 815]
        [cite_start]"original_message_chat_id": query.message.chat_id, # [cite: 815]
        [cite_start]"original_message_message_id": query.message.message_id, # [cite: 815]
        # Determine where to go back if user cancels input
        [cite_start]"previous_callback": CallbackBuilder.create(namespace, "show_menu" if namespace == CallbackNamespace.EXIT_STRATEGY else f"{action.split('_')[0]}_menu", rec_id) # [cite: 815]
    }

    # Define prompts based on action
    [cite_start]prompts = { # [cite: 815]
        [cite_start]"edit_sl": "✏️ Send the new Stop Loss price:", # [cite: 815]
        [cite_start]"edit_tp": "🎯 Send the new list of Targets (e.g., 50k 52k@50):", # [cite: 816]
        [cite_start]"edit_entry": "💰 Send the new Entry price (only for PENDING):", # [cite: 816]
        [cite_start]"edit_notes": "📝 Send the new Notes (or send 'clear' to remove):", # [cite: 816]
        [cite_start]"close_manual": "✍️ Send the final Exit Price:", # [cite: 816]
        [cite_start]"set_fixed": "🔒 Send the fixed Profit Stop price:", # [cite: 816]
        [cite_start]"set_trailing": "📈 Send the Trailing Stop distance (e.g., 1.5% or 500):", # [cite: 816]
        [cite_start]"partial_close_custom": "💰 Send the custom partial close Percentage (e.g., 30):" # [cite: 817]
    }
    [cite_start]prompt_text = prompts.get(action, 'Send the new value:') # [cite: 817]

    # Keyboard with just a cancel button during input
    [cite_start]cancel_button = InlineKeyboardButton( # [cite: 817]
        [cite_start]"❌ Cancel Input", # [cite: 817]
        # Use generic mgmt:cancel_input action
        [cite_start]callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id) # [cite: 817]
    )
    [cite_start]input_keyboard = InlineKeyboardMarkup([[cancel_button]]) # [cite: 817]

    [cite_start]await safe_edit_message( # [cite: 817]
        [cite_start]context.bot, query.message.chat_id, query.message.message_id, # [cite: 817]
        [cite_start]text=f"{query.message.text_html}\n\n<b>{prompt_text}</b>", # Append prompt [cite: 817]
        [cite_start]reply_markup=input_keyboard # [cite: 817]
    )
    # No return needed, default state transition handled by ConversationHandler setup

[cite_start]@uow_transaction # [cite: 817]
[cite_start]@require_active_user # [cite: 817]
# Require analyst only if action requires it (most do)
# @require_analyst_user # Applied conditionally inside
[cite_start]async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs): # [cite: 818]
    """Handles text reply with new value, validates, asks for confirmation."""
    [cite_start]if await handle_management_timeout(update, context): return ConversationHandler.END # [cite: 818]
    [cite_start]update_management_activity(context) # [cite: 818]

    [cite_start]state = context.user_data.get(AWAITING_INPUT_KEY) # [cite: 818]

    # ✅ FIX: Check for state and message reply, get chat/message IDs
    [cite_start]if not (state and update.message and update.message.reply_to_message): # [cite: 818]
        [cite_start]log.debug("Reply handler ignored: No valid state or not a reply.") # [cite: 818]
        # Don't delete message if it wasn't meant for the bot
        [cite_start]return # Ignore message [cite: 819]

    [cite_start]chat_id = state.get("original_message_chat_id") # [cite: 819]
    [cite_start]message_id = state.get("original_message_message_id") # [cite: 819]

    [cite_start]if not (chat_id and message_id): # [cite: 819]
        [cite_start]log.error(f"Reply handler for user {update.effective_user.id} has corrupt state: missing message IDs.") # [cite: 819]
        [cite_start]context.user_data.pop(AWAITING_INPUT_KEY, None) # Clear corrupt state [cite: 819]
        # Maybe send a new error message?
        [cite_start]return # Cannot proceed [cite: 819]

    [cite_start]namespace = state.get("namespace") # [cite: 819]
    [cite_start]action = state.get("action") # [cite: 819]
    [cite_start]item_id = state.get("item_id") # [cite: 819]
    [cite_start]item_type = state.get("item_type", 'rec') # Default to 'rec' [cite: 819]
    [cite_start]user_input = update.message.text.strip() if update.message.text else "" # [cite: 819]

    # Check if user is analyst IF the action requires it
    [cite_start]is_analyst_action = namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value] # [cite: 819]
    [cite_start]if is_analyst_action and (not db_user or db_user.user_type != UserTypeEntity.ANALYST): # [cite: 819]
         [cite_start]await update.message.reply_text("🚫 Permission Denied: This action requires Analyst role.") # [cite: 819]
         # Clean state but don't delete messages, just ignore input
         [cite_start]context.user_data.pop(AWAITING_INPUT_KEY, None) # [cite: 819]
         [cite_start]return # Ignore input [cite: 820]

    # --- Safely delete user's reply ---
    [cite_start]try: await update.message.delete() # [cite: 820]
    [cite_start]except Exception: log.debug("Could not delete user reply message.") # [cite: 820]

    # --- Validate Input and Prepare Change ---
    [cite_start]validated_value: Any = None # Use Any to store various types [cite: 820]
    [cite_start]change_description = "" # For the confirmation message [cite: 820]
    [cite_start]error_message = None # [cite: 820]
    [cite_start]trade_service = get_service(context, "trade_service", TradeService) # Needed for validation logic access [cite: 820]

    [cite_start]try: # [cite: 820]
        # Fetch current item state for validation context
        [cite_start]current_item = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), item_type, item_id) # [cite: 820]
        [cite_start]if not current_item: raise ValueError("Position not found or closed.") # [cite: 820]

        # --- Input Validation Logic ---
        [cite_start]if namespace == CallbackNamespace.EXIT_STRATEGY.value: # [cite: 820]
            [cite_start]if action == "set_fixed": # [cite: 821]
                 [cite_start]price = parse_number(user_input) # [cite: 821]
                 [cite_start]if price is None: raise ValueError("Invalid price format.") # [cite: 821]
                 # Add validation: Fixed price must be profitable vs entry
                 [cite_start]entry_dec = _to_decimal(_get_attr(current_item.entry, 'value')) # [cite: 821]
                 if (_get_attr(current_item.side, 'value') == 'LONG' and price <= entry_dec) or \
                    (_get_attr(current_item.side, 'value') [cite_start]== 'SHORT' and price >= entry_dec): # [cite: 821]
                      [cite_start]raise ValueError("Fixed profit stop price must be beyond entry price.") # [cite: 821]
                 [cite_start]validated_value = {"mode": "FIXED", "price": price} # [cite: 821]
                 [cite_start]change_description = f"Activate Fixed Profit Stop at {_format_price(price)}" # [cite: 821]
            [cite_start]elif action == "set_trailing": # [cite: 821]
                 [cite_start]config = parse_trailing_distance(user_input) # [cite: 822]
                 [cite_start]if config is None: raise ValueError("Invalid format. Use % (e.g., '1.5%') or value (e.g., '500').") # [cite: 823]
                 [cite_start]validated_value = {"mode": "TRAILING", "trailing_value": config["value"]} # Store Decimal [cite: 823]
                 [cite_start]change_description = f"Activate Trailing Stop with distance {user_input}" # [cite: 823]

        [cite_start]elif namespace == CallbackNamespace.RECOMMENDATION.value: # [cite: 823]
            # Actions require analyst role already checked above
            [cite_start]if action in ["edit_sl", "edit_entry", "close_manual"]: # [cite: 823]
                [cite_start]price = parse_number(user_input) # [cite: 823]
                [cite_start]if price is None: raise ValueError("Invalid price format.") # [cite: 824]
                [cite_start]if action == "edit_sl": # [cite: 824]
                    # Validate against current state (using _validate_recommendation_data)
                    [cite_start]trade_service._validate_recommendation_data( # [cite: 824]
                         [cite_start]_get_attr(current_item.side, 'value'), _get_attr(current_item.entry, 'value'), price, current_item.targets.values # [cite: 824]
                    )
                    [cite_start]validated_value = price # [cite: 825]
                    [cite_start]change_description = f"Update Stop Loss to {_format_price(price)}" # [cite: 825]
                [cite_start]elif action == "edit_entry": # [cite: 825]
                     [cite_start]if current_item.status != RecommendationStatus.PENDING: raise ValueError("Entry can only be edited for PENDING signals.") # [cite: 825]
                     [cite_start]trade_service._validate_recommendation_data( # [cite: 826]
                          [cite_start]_get_attr(current_item.side, 'value'), price, _get_attr(current_item.stop_loss, 'value'), current_item.targets.values # [cite: 826]
                     )
                     [cite_start]validated_value = price # [cite: 826]
                     [cite_start]change_description = f"Update Entry Price to {_format_price(price)}" # [cite: 826]
                [cite_start]elif action == "close_manual": # [cite: 827]
                     [cite_start]validated_value = price # [cite: 827]
                     [cite_start]change_description = f"Manually Close Position at {_format_price(price)}" # [cite: 827]
            [cite_start]elif action == "edit_tp": # [cite: 827]
                # parse_targets_list expects list of strings
                [cite_start]targets_list_dict = parse_targets_list(user_input.split()) # Returns list[dict] with Decimal [cite: 828]
                [cite_start]if not targets_list_dict: raise ValueError("Invalid targets format or no valid targets found.") # [cite: 828]
                # Validate new targets against current state
                [cite_start]trade_service._validate_recommendation_data( # [cite: 828]
                     [cite_start]_get_attr(current_item.side, 'value'), _get_attr(current_item.entry, 'value'), _get_attr(current_item.stop_loss, 'value'), targets_list_dict # [cite: 828]
                )
                [cite_start]validated_value = targets_list_dict # [cite: 828]
                # ✅ FIX: Correct f-string syntax (avoids backslash issue by separating list comprehension)
                [cite_start]price_strings = [_format_price(t['price']) for t in validated_value] # Use double quotes for key [cite: 829]
                [cite_start]change_description = f"Update Targets to: {', '.join(price_strings)}" # [cite: 829]
            [cite_start]elif action == "edit_notes": # [cite: 829]
                 [cite_start]if user_input.lower() in ['clear', 'مسح', 'remove', 'إزالة', '']: # [cite: 830]
                      [cite_start]validated_value = None # Represent clearing notes [cite: 830]
                      [cite_start]change_description = "Clear Notes" # [cite: 830]
                 [cite_start]else: # [cite: 830]
                      [cite_start]validated_value = user_input # Store as string [cite: 830]
                      [cite_start]change_description = f"Update Notes to: '{_truncate_text(validated_value, 50)}'" # [cite: 831]
            [cite_start]elif action == "partial_close_custom": # [cite: 831]
                 [cite_start]percent_val = parse_number(user_input.replace('%','')) # Returns Decimal [cite: 831]
                 [cite_start]if percent_val is None or not (0 < percent_val <= Decimal('100')): # [cite: 831]
                     [cite_start]raise ValueError("Percentage must be a number between 0 and 100.") # [cite: 832]
                 [cite_start]validated_value = percent_val # Store as Decimal [cite: 832]
                 [cite_start]change_description = f"Partially Close {percent_val:g}% of position at Market Price" # [cite: 832]

        # --- If Validation Passed ---
        [cite_start]if validated_value is not None or action == "edit_notes": # Allow clearing notes [cite: 832]
            # Store validated value temporarily, clear prompt state
            [cite_start]context.user_data[PENDING_CHANGE_KEY] = {"value": validated_value} # [cite: 832]
            [cite_start]context.user_data.pop(AWAITING_INPUT_KEY, None) # [cite: 833]

            # Build confirmation keyboard
            [cite_start]confirm_callback = CallbackBuilder.create("mgmt", "confirm_change", namespace, action, item_id) # [cite: 833]
            # Use previous_callback stored in state for "Re-enter"
            [cite_start]reenter_callback = state.get("previous_callback", CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, item_id)) # Fallback to show panel [cite: 833]
            [cite_start]cancel_callback = CallbackBuilder.create("mgmt", "cancel_all", item_id) # Generic cancel [cite: 834]

            [cite_start]confirm_keyboard = InlineKeyboardMarkup([ # [cite: 834]
                [cite_start][InlineKeyboardButton(ButtonTexts.CONFIRM, callback_data=confirm_callback)], # [cite: 834]
                [cite_start][InlineKeyboardButton("✏️ Re-enter Value", callback_data=reenter_callback)], # [cite: 834]
                [cite_start][InlineKeyboardButton(ButtonTexts.CANCEL + " Action", callback_data=cancel_callback)], # [cite: 834]
            ])
            # ✅ FIX: Use new safe_edit_message signature
            [cite_start]await safe_edit_message(context.bot, chat_id, message_id, text=f"❓ <b>Confirm Action</b>\n\nDo you want to:\n➡️ {change_description}?", reply_markup=confirm_keyboard) # [cite: 834]
        [cite_start]else: # [cite: 834]
             # Should ideally not be reached if validation logic is correct
             [cite_start]raise ValueError("Validation passed but no value was stored.") # [cite: 834]

    [cite_start]except ValueError as e: # [cite: 834]
        [cite_start]log.warning(f"Invalid input during reply for {action} on {item_type} #{item_id}: {e}") # [cite: 835]
        # Re-prompt, keeping state AWAITING_INPUT_KEY active
        [cite_start]cancel_button = InlineKeyboardButton("❌ Cancel Input", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", item_id)) # [cite: 835]
        # Fetch original prompt text if possible (might be complex without storing it)
        # For simplicity, use a generic re-prompt message
        # ✅ FIX: Use new safe_edit_message signature
        # We need the original message text. Let's fetch it (less ideal) or reconstruct
        # Reconstruct prompt for simplicity
        prompts = { "edit_sl": "✏️ Send the new Stop Loss price:", # ... (copy prompts dict from prompt_handler) ...
                   "partial_close_custom": "💰 Send the custom partial close Percentage (e.g., 30):"}
        prompt_text = prompts.get(action, 'Send the new value:')
        [cite_start]await safe_edit_message( # [cite: 835]
            [cite_start]context.bot, chat_id, message_id, # [cite: 836]
            [cite_start]text=f"⚠️ **Invalid Input:** {e}\n\n<b>{prompt_text}</b>", # [cite: 836]
            [cite_start]reply_markup=InlineKeyboardMarkup([[cancel_button]]) # [cite: 836]
        )
        # Stay in implicit state waiting for reply

    [cite_start]except Exception as e: # [cite: 837]
        [cite_start]loge.error(f"Error processing reply for {action} on {item_type} #{item_id}: {e}", exc_info=True) # [cite: 837]
        [cite_start]await context.bot.send_message( # Send new message on unexpected error [cite: 837]
             [cite_start]chat_id=chat_id, # [cite: 837]
             text=f"❌ Unexpected error processing input: {e}\nOperation cancelled." [cite_start]# [cite: 837]
        )
        [cite_start]clean_management_state(context) # [cite: 837]
        # Attempt to show the main panel again
        [cite_start]await _send_or_edit_position_panel(update, context, db_session, item_type, item_id) # [cite: 837]
        # No return needed as we are not in a ConversationHandler state


# --- Confirmation & Cancellation Handlers ---
[cite_start]@uow_transaction # [cite: 837]
[cite_start]@require_active_user # [cite: 837]
# Apply analyst check conditionally based on action
[cite_start]async def confirm_change_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs): # [cite: 838]
    """Executes the pending change after user confirmation."""
    [cite_start]query = update.callback_query # [cite: 838]
    [cite_start]await query.answer("Processing...") # [cite: 838]
    [cite_start]if await handle_management_timeout(update, context): return # [cite: 838]

    [cite_start]pending_data = context.user_data.pop(PENDING_CHANGE_KEY, None) # [cite: 838]
    [cite_start]parsed_data = CallbackBuilder.parse(query.data) # mgmt:confirm_change:namespace:action:item_id [cite: 838]
    [cite_start]params = parsed_data.get('params', []) # [cite: 838]
    [cite_start]item_id = None # [cite: 838]
    [cite_start]item_type = 'rec' # Default [cite: 838]
    [cite_start]try: # [cite: 838]
        [cite_start]if len(params) >= 3: # [cite: 838]
            [cite_start]namespace, action, item_id_str = params[0], params[1], params[2] # [cite: 838]
            [cite_start]item_id = int(item_id_str) # [cite: 838]
            [cite_start]item_type = 'rec' if namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value] else 'trade' # Determine type [cite: 838]
        [cite_start]else: raise ValueError("Invalid confirmation callback format") # [cite: 838]

        [cite_start]if not pending_data or "value" not in pending_data: # [cite: 838]
            [cite_start]raise ValueError("No pending change found or data corrupt.") # [cite: 839]

        # --- Conditional Analyst Check ---
        [cite_start]is_analyst_action = namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value] # [cite: 839]
        [cite_start]if is_analyst_action and (not db_user or db_user.user_type != UserTypeEntity.ANALYST): # [cite: 839]
             [cite_start]raise ValueError("Permission Denied: Analyst role required.") # [cite: 839]

        [cite_start]pending_value = pending_data["value"] # [cite: 839]
        [cite_start]trade_service = get_service(context, "trade_service", TradeService) # [cite: 839]
        [cite_start]user_telegram_id = str(db_user.telegram_user_id) # [cite: 839]
        [cite_start]success = False # [cite: 839]

        # --- Execute Service Call based on Namespace and Action ---
        [cite_start]if namespace == CallbackNamespace.EXIT_STRATEGY.value: # [cite: 839]
            [cite_start]mode = pending_value["mode"] # [cite: 839]
            [cite_start]price = pending_value.get("price") # [cite: 840]
            [cite_start]trailing = pending_value.get("trailing_value") # [cite: 840]
            [cite_start]await trade_service.set_exit_strategy_async(item_id, user_telegram_id, mode, price=price, trailing_value=trailing, active=True, session=db_session) # [cite: 840]
            [cite_start]success = True # [cite: 840]
        [cite_start]elif namespace == CallbackNamespace.RECOMMENDATION.value: # [cite: 840]
            [cite_start]if action == "edit_sl": await trade_service.update_sl_for_user_async(item_id, user_telegram_id, pending_value, db_session); success = True # [cite: 840]
            [cite_start]elif action == "edit_entry": await trade_service.update_entry_and_notes_async(item_id, user_telegram_id, new_entry=pending_value, new_notes=None, db_session=db_session); success = True # [cite: 840]
            [cite_start]elif action == "close_manual": await trade_service.close_recommendation_async(item_id, user_telegram_id, pending_value, db_session, reason="MANUAL_PRICE_CLOSE"); success = True # [cite: 840]
            [cite_start]elif action == "edit_tp": await trade_service.update_targets_for_user_async(item_id, user_telegram_id, pending_value, db_session); success = True # [cite: 841]
            [cite_start]elif action == "edit_notes": await trade_service.update_entry_and_notes_async(item_id, user_telegram_id, new_entry=None, new_notes=pending_value, db_session=db_session); success = True # [cite: 841]
            [cite_start]elif action == "partial_close_custom": # [cite: 841]
                 [cite_start]price_service = get_service(context, "price_service", PriceService) # [cite: 841]
                 [cite_start]rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, 'rec', item_id) # [cite: 841]
                 [cite_start]if not rec: raise ValueError("Recommendation not found.") # [cite: 841]
                 [cite_start]live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True) # [cite: 842]
                 [cite_start]if not live_price: raise ValueError(f"Could not fetch market price for {rec.asset.value}.") # [cite: 842]
                 [cite_start]await trade_service.partial_close_async(item_id, user_telegram_id, pending_value, Decimal(str(live_price)), db_session, triggered_by="MANUAL_CUSTOM"); success = True # [cite: 842]

        # --- If successful, update the panel ---
        [cite_start]if success: # [cite: 842]
             [cite_start]await query.answer("✅ Action Successful!") # [cite: 842]
             [cite_start]await _send_or_edit_position_panel(update, context, db_session, item_type, item_id) # [cite: 842]
        # Error handling is done via the except block

    [cite_start]except (ValueError, Exception) as e: # [cite: 842]
        [cite_start]loge.error(f"Error confirming change for {action} on {item_type} #{item_id}: {e}", exc_info=True) # [cite: 843]
        [cite_start]try: await query.answer(f"❌ Execution Failed: {str(e)[:150]}", show_alert=True) # Show alert on failure [cite: 843]
        [cite_start]except TelegramError: pass # [cite: 843]
        # Attempt to show panel again even on failure
        [cite_start]if item_id: await _send_or_edit_position_panel(update, context, db_session, item_type, item_id) # [cite: 843]
    [cite_start]finally: # [cite: 843]
        # Always clean state after confirmation attempt
        [cite_start]clean_management_state(context) # [cite: 843]

# --- Cancel Handlers ---
[cite_start]@uow_transaction # [cite: 843]
[cite_start]@require_active_user # [cite: 843]
[cite_start]async def cancel_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs): # [cite: 844]
    """Handles cancellation during the text input phase by returning to the relevant panel."""
    [cite_start]query = update.callback_query # [cite: 844]
    [cite_start]await query.answer("Input cancelled.") # [cite: 844]
    [cite_start]if await handle_management_timeout(update, context): return # [cite: 844]

    [cite_start]state = context.user_data.pop(AWAITING_INPUT_KEY, None) # [cite: 844]
    [cite_start]context.user_data.pop(PENDING_CHANGE_KEY, None) # Also clear any pending change [cite: 844]

    # Determine item_id and type from callback or state
    [cite_start]item_id = None # [cite: 844]
    [cite_start]item_type = 'rec' # Default [cite: 844]
    [cite_start]if state: # [cite: 844]
        [cite_start]item_id = state.get("item_id") # [cite: 844]
        [cite_start]item_type = state.get("item_type", 'rec') # [cite: 844]
    [cite_start]elif query and query.data: # [cite: 844]
         # Fallback: parse from cancel callback mgmt:cancel_input:<item_id>
         [cite_start]params = CallbackBuilder.parse(query.data).get('params', []) # [cite: 844]
         [cite_start]if params and params[0].isdigit(): item_id = int(params[0]) # [cite: 844]
         # Assume 'rec' if type not in callback

    [cite_start]if item_id is not None: # [cite: 844]
         # Restore the view before input was requested
         [cite_start]await _send_or_edit_position_panel(update, context, db_session, item_type, item_id) # [cite: 845]
    [cite_start]elif query and query.message: # Fallback if state/callback is corrupt [cite: 845]
        [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Input cancelled.") # [cite: 845]


[cite_start]@uow_transaction # [cite: 845]
[cite_start]@require_active_user # [cite: 845]
[cite_start]async def cancel_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs): # [cite: 845]
    """Handles cancellation during confirmation, returning to the main panel."""
    [cite_start]query = update.callback_query # [cite: 845]
    [cite_start]await query.answer("Action cancelled.") # [cite: 845]
    [cite_start]if await handle_management_timeout(update, context): return # [cite: 845]

    [cite_start]clean_management_state(context) # Clean all mgmt state [cite: 845]

    # Determine item_id and type from callback mgmt:cancel_all:<item_id>
    [cite_start]item_id = None # [cite: 845]
    [cite_start]item_type = 'rec' # Assume default [cite: 845]
    [cite_start]if query and query.data: # [cite: 845]
         [cite_start]params = CallbackBuilder.parse(query.data).get('params', []) # [cite: 845]
         [cite_start]if params and params[0].isdigit(): item_id = int(params[0]) # [cite: 845]
         # Could try fetching item to determine type if needed, but 'rec' is common

    [cite_start]if item_id is not None: # [cite: 845]
         [cite_start]await _send_or_edit_position_panel(update, context, db_session, item_type, item_id) # [cite: 845]
    [cite_start]elif query and query.message: # Fallback [cite: 845]
        [cite_start]await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Action cancelled.") # [cite: 845]


# --- Immediate Action Handlers ---
[cite_start]@uow_transaction # [cite: 845]
[cite_start]@require_active_user # [cite: 846]
[cite_start]@require_analyst_user # Most immediate actions are analyst-only [cite: 846]
[cite_start]async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs): # [cite: 846]
    """Handles actions executing immediately (Move BE, Cancel Strategy, Close Market)."""
    [cite_start]query = update.callback_query # [cite: 846]
    [cite_start]await query.answer("Processing...") # [cite: 846]
    [cite_start]if await handle_management_timeout(update, context): return # [cite: 846]
    [cite_start]update_management_activity(context) # [cite: 846]

    [cite_start]parsed_data = CallbackBuilder.parse(query.data) # [cite: 846]
    [cite_start]namespace = parsed_data.get('namespace') # [cite: 846]
    [cite_start]action = parsed_data.get('action') # [cite: 846]
    [cite_start]params = parsed_data.get('params', []) # [cite: 846]
    [cite_start]rec_id = int(params[0]) if params and params[0].isdigit() else None # [cite: 846]

    [cite_start]if rec_id is None: # [cite: 846]
        [cite_start]loge.error(f"Could not get rec_id from immediate action callback: {query.data}") # [cite: 847]
        [cite_start]await query.answer("❌ Invalid request.", show_alert=True) # [cite: 847]
        [cite_start]return # [cite: 847]

    [cite_start]trade_service = get_service(context, "trade_service", TradeService) # [cite: 847]
    [cite_start]user_telegram_id = str(db_user.telegram_user_id) # [cite: 847]
    [cite_start]success_message = None # [cite: 847]
    [cite_start]item_type = 'rec' # Assume rec for these actions [cite: 847]

    [cite_start]try: # [cite: 847]
        # Fetch fresh state first
        [cite_start]rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, item_type, rec_id) # [cite: 847]
        [cite_start]if not rec: raise ValueError("Recommendation not found or closed.") # [cite: 847]
        # Ensure action is valid for current status
        [cite_start]if action != "cancel" and rec.status != RecommendationStatus.ACTIVE: # [cite: 847]
             [cite_start]raise ValueError(f"Action '{action}' requires ACTIVE status (current: {rec.status.value}).") # [cite: 847]

        # --- Execute Action ---
        [cite_start]if namespace == CallbackNamespace.EXIT_STRATEGY.value: # [cite: 847]
            [cite_start]if action == "move_to_be": # [cite: 848]
                [cite_start]await trade_service.move_sl_to_breakeven_async(rec_id, db_session) # [cite: 848]
                success_message = "✅ SL moved to Break Even." [cite_start]# [cite: 848]
            [cite_start]elif action == "cancel": # [cite: 848]
                 # Check if a strategy is actually active before cancelling
                 # Use the fetched entity which now includes profit stop fields
                 [cite_start]if getattr(rec, 'profit_stop_active', False): # [cite: 848]
                    [cite_start]await trade_service.set_exit_strategy_async(rec_id, user_telegram_id, "NONE", active=False, session=db_session) # [cite: 849]
                    success_message = "❌ Automated exit strategy cancelled." [cite_start]# [cite: 849]
                 [cite_start]else: # [cite: 849]
                     success_message = "ℹ️ No active exit strategy to cancel." # [cite_start]Informative message [cite: 849]

        [cite_start]elif namespace == CallbackNamespace.RECOMMENDATION.value: # [cite: 849]
             [cite_start]if action == "close_market": # [cite: 849]
                [cite_start]price_service = get_service(context, "price_service", PriceService) # [cite: 849]
                [cite_start]live_price = None # [cite: 850]
                [cite_start]try: # [cite: 850]
                    [cite_start]await query.answer("Fetching price...") # [cite: 850]
                    [cite_start]live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True) # [cite: 850]
                    [cite_start]if not live_price: raise ValueError(f"Could not fetch market price for {rec.asset.value}.") # [cite: 851]
                [cite_start]except Exception as price_err: # [cite: 851]
                    [cite_start]loge.error(f"Failed to get live price for close_market #{rec_id}: {price_err}") # [cite: 851]
                    [cite_start]await query.answer(f"❌ Price Fetch Failed: {price_err}", show_alert=True) # [cite: 851]
                    [cite_start]return # [cite: 851]

                [cite_start]try: # [cite: 852]
                    [cite_start]await query.answer("Closing...") # [cite: 852]
                    [cite_start]await trade_service.close_recommendation_async(rec_id, user_telegram_id, Decimal(str(live_price)), db_session, reason="MARKET_CLOSE_MANUAL") # [cite: 852]
                    success_message = f"✅ Position closed at market price ~{_format_price(live_price)}." [cite_start]# [cite: 852]
                [cite_start]except Exception as close_err: # [cite: 852]
                    [cite_start]loge.error(f"Failed to close recommendation #{rec_id} via close_market: {close_err}", exc_info=True) # [cite: 853]
                    # Re-raise to be caught by the outer handler
                    [cite_start]raise close_err # [cite: 853]

        # --- If successful, show message and update panel ---
        [cite_start]if success_message: await query.answer(success_message) # [cite: 853]
        [cite_start]await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id) # [cite: 853]

    [cite_start]except (ValueError, Exception) as e: # [cite: 853]
        error_text = f"❌ Action Failed: {str(e)[:150]}" # Truncate long errors
        [cite_start]loge.error(f"Error in immediate action {namespace}:{action} for {item_type} #{rec_id}: {e}", exc_info=True) # [cite: 854]
        [cite_start]try: await query.answer(error_text, show_alert=True) # [cite: 854]
        [cite_start]except TelegramError: pass # [cite: 854]
        # Refresh panel even on failure
        [cite_start]if rec_id: await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id) # [cite: 854]
    [cite_start]finally: # [cite: 854]
        # Clean state? Usually not needed for immediate actions, but maybe pending?
        context.user_data.pop(PENDING_CHANGE_KEY, None) # Clear just in case
        # Don't clean AWAITING_INPUT here


[cite_start]@uow_transaction # [cite: 854]
[cite_start]@require_active_user # [cite: 854]
[cite_start]@require_analyst_user # Analyst action [cite: 855]
[cite_start]async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs): # [cite: 855]
    """Handles partial close buttons with fixed percentages."""
    [cite_start]query = update.callback_query # [cite: 855]
    [cite_start]await query.answer("Processing...") # [cite: 855]
    [cite_start]if await handle_management_timeout(update, context): return # [cite: 855]
    [cite_start]update_management_activity(context) # [cite: 855]

    [cite_start]parsed_data = CallbackBuilder.parse(query.data) # rec:pt:<rec_id>:<percentage> [cite: 855]
    [cite_start]params = parsed_data.get('params', []) # [cite: 855]
    [cite_start]rec_id, close_percent_str = None, None # [cite: 855]
    [cite_start]item_type = 'rec' # Assume rec [cite: 855]
    [cite_start]try: # [cite: 855]
         [cite_start]if len(params) >= 2: # [cite: 855]
              [cite_start]rec_id = int(params[0]) # [cite: 855]
              [cite_start]close_percent_str = params[1] # [cite: 855]
              [cite_start]close_percent = Decimal(close_percent_str) # [cite: 856]
              [cite_start]if not (0 < close_percent <= 100): raise ValueError("Invalid percentage") # [cite: 856]
         [cite_start]else: raise ValueError("Invalid callback format") # [cite: 856]
    [cite_start]except (ValueError, IndexError, TypeError) as e: # [cite: 856]
         [cite_start]loge.error(f"Could not parse partial close fixed callback: {query.data}, error: {e}") # [cite: 856]
         [cite_start]await query.answer("❌ Invalid request.", show_alert=True) # [cite: 856]
         [cite_start]return # [cite: 856]

    [cite_start]trade_service = get_service(context, "trade_service", TradeService) # [cite: 856]
    [cite_start]price_service = get_service(context, "price_service", PriceService) # [cite: 856]
    [cite_start]user_telegram_id = str(db_user.telegram_user_id) # [cite: 856]

    [cite_start]try: # [cite: 856]
        [cite_start]rec = trade_service.get_position_details_for_user(db_session, user_telegram_id, item_type, rec_id) # [cite: 857]
        [cite_start]if not rec: raise ValueError("Recommendation not found.") # [cite: 857]
        [cite_start]if rec.status != RecommendationStatus.ACTIVE: raise ValueError("Can only partially close ACTIVE positions.") # [cite: 857]

        # Fetch live price
        [cite_start]live_price = None # [cite: 857]
        [cite_start]try: # [cite: 857]
            [cite_start]await query.answer("Fetching price...") # [cite: 857]
            [cite_start]live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True) # [cite: 857]
            [cite_start]if not live_price: raise ValueError(f"Could not fetch market price for {rec.asset.value}.") # [cite: 857]
        [cite_start]except Exception as price_err: # [cite: 857]
            [cite_start]loge.error(f"Failed to get live price for partial_close_fixed #{rec_id}: {price_err}") # [cite: 857]
            [cite_start]await query.answer(f"❌ Price Fetch Failed: {price_err}", show_alert=True) # [cite: 857]
            [cite_start]return # [cite: 857]

        # Execute partial close
        [cite_start]await trade_service.partial_close_async( # [cite: 857]
            [cite_start]rec_id, user_telegram_id, close_percent, Decimal(str(live_price)), db_session, triggered_by="MANUAL_FIXED" # [cite: 857]
        )
        [cite_start]await query.answer(f"✅ Closed {close_percent:g}% at market price ~{_format_price(live_price)}.") # [cite: 857]

        # Update panel
        [cite_start]await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id) # [cite: 857]
    [cite_start]except (ValueError, Exception) as e: # [cite: 857]
        [cite_start]loge.error(f"Error in partial close fixed handler for rec #{rec_id}: {e}", exc_info=True) # [cite: 857]
        [cite_start]try: await query.answer(f"❌ Partial Close Failed: {str(e)[:150]}", show_alert=True) # [cite: 857]
        [cite_start]except TelegramError: pass # [cite: 857]
        # Refresh panel even on failure
        [cite_start]await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id) # [cite: 858]
    [cite_start]finally: # [cite: 858]
         # Clean state? Not strictly necessary for immediate actions but good practice
         [cite_start]clean_management_state(context) # [cite: 858]


# --- Handler Registration ---
[cite_start]def register_management_handlers(app: Application): # [cite: 858]
    """Registers all management handlers including conversations."""
    # --- Entry Point Command ---
    [cite_start]app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler)) # [cite: 858]

    # --- Main Callback Handlers (Group 1 - After Conversations) ---
    [cite_start]app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{CallbackNamespace.NAVIGATION.value}:{CallbackAction.NAVIGATE.value}:"), group=1) # [cite: 858]
    # Show Panel (Handles Rec/Trade Show and Back buttons)
    [cite_start]app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"), group=1) # [cite: 858]
    # Show Submenus (Analyst only)
    [cite_start]app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_menu|close_menu|partial_close_menu|show_menu):"), group=1) # [cite: 858]
    # Prompt for input (Triggers implicit conversation via AWAITING_INPUT_KEY)
    [cite_start]app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_|set_|close_manual|partial_close_custom)"), group=1) # [cite: 858]
    # Confirm change action (Executes change)
    [cite_start]app.add_handler(CallbackQueryHandler(confirm_change_handler, pattern=rf"^mgmt:confirm_change:"), group=1) # [cite: 858]
    # Cancel Input / All (Cleans state and returns to panel)
    [cite_start]app.add_handler(CallbackQueryHandler(cancel_input_handler, pattern=rf"^mgmt:cancel_input:"), group=1) # [cite: 858]
    [cite_start]app.add_handler(CallbackQueryHandler(cancel_all_handler, pattern=rf"^mgmt:cancel_all:"), group=1) # [cite: 858]
    # Immediate Actions (Analyst only)
    [cite_start]app.add_handler(CallbackQueryHandler(immediate_action_handler, pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}:(?:move_to_be|cancel):|{CallbackNamespace.RECOMMENDATION.value}:close_market)"), group=1) # [cite: 858]
    # Partial Close Fixed Percentages (Analyst only)
    [cite_start]app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:{CallbackAction.PARTIAL.value}:\d+:(?:25|50)$"), group=1) # [cite: 858]

    # --- Conversation Handler for User Input (Replies) ---
    # Catches replies when AWAITING_INPUT_KEY is set.
    [cite_start]app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, reply_handler), group=0) # [cite: 858]

    # --- Conversation Handler for Custom Partial Close ---
    [cite_start]partial_close_conv = ConversationHandler( # [cite: 858]
        [cite_start]entry_points=[CallbackQueryHandler(partial_close_custom_start, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:partial_close_custom:")], # [cite: 858]
        [cite_start]states={ # [cite: 858]
            [cite_start]AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_percent_received)], # [cite: 858]
            [cite_start]AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_price_received)], # [cite: 858]
        },
        [cite_start]fallbacks=[ # [cite: 858]
             [cite_start]CommandHandler("cancel", partial_close_cancel), # [cite: 859]
             [cite_start]CallbackQueryHandler(partial_close_cancel, pattern=rf"^mgmt:cancel_input:") # Reuse cancel button logic [cite: 859]
        ],
        [cite_start]name="partial_close_conversation", # [cite: 859]
        [cite_start]per_user=True, per_chat=True, conversation_timeout=MANAGEMENT_TIMEOUT, persistent=False, # [cite: 859]
    )
    [cite_start]app.add_handler(partial_close_conv, group=0) # Needs priority to capture input [cite: 859]

    # --- Conversation Handler for User Trade Closing ---
    [cite_start]user_trade_close_conv = ConversationHandler( # [cite: 859]
        [cite_start]entry_points=[CallbackQueryHandler(user_trade_close_start, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.CLOSE.value}:trade:")], # [cite: 859]
        [cite_start]states={ # [cite: 859]
            [cite_start]AWAIT_USER_TRADE_CLOSE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, user_trade_close_price_received)], # [cite: 859]
        },
        [cite_start]fallbacks=[ # [cite: 859]
            [cite_start]CommandHandler("cancel", cancel_user_trade_close), # [cite: 859]
            # Reuse generic cancel button logic
            [cite_start]CallbackQueryHandler(cancel_user_trade_close, pattern=rf"^mgmt:cancel_input:") # [cite: 859]
        ],
        [cite_start]name="user_trade_close_conversation", # [cite: 859]
        [cite_start]per_user=True, per_chat=True, conversation_timeout=MANAGEMENT_TIMEOUT, persistent=False, # [cite: 859]
    )
    [cite_start]app.add_handler(user_trade_close_conv, group=0) # Needs priority [cite: 859]

# --- END of management handlers ---