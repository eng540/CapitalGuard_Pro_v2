# --- src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---
"""
Handles the user flow for parsing a forwarded text message, including review and correction (v2.0.1 - Warning Suppress).
Uses ConversationHandler for state management during review/edit.
Integrates with ParsingService v4.1+ and TradeService v31.0+.
Includes logic for suggesting template saves.
✅ FIX: Added `per_message=False` to ConversationHandler registration to suppress PTBUserWarning noise in logs.
"""

import asyncio
import logging
import time
import json  # For diff comparison if needed
from decimal import Decimal
from typing import Dict, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters,
    ConversationHandler, CommandHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest

# Infrastructure & Application specific imports
from capitalguard.infrastructure.db.uow import session_scope, uow_transaction
from capitalguard.interfaces.telegram.helpers import get_service, parse_cq_parts
from capitalguard.interfaces.telegram.auth import require_active_user, get_db_user
from capitalguard.application.services.parsing_service import ParsingService, ParsingResult
from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.telegram.keyboards import (
    CallbackBuilder, CallbackNamespace, CallbackAction, build_confirmation_keyboard,
    build_editable_review_card, ButtonTexts
)
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list

# Import safe_edit_message and timeout helpers from management_handlers
from capitalguard.interfaces.telegram.management_handlers import (
    safe_edit_message, handle_management_timeout, update_management_activity, MANAGEMENT_TIMEOUT
)

# Import repository/models for direct DB checks if needed
from capitalguard.infrastructure.db.repository import ParsingRepository
from capitalguard.infrastructure.db.models import ParsingAttempt

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")  # Error logger

# --- Conversation States ---
AWAIT_REVIEW, AWAIT_CORRECTION_VALUE, AWAIT_SAVE_TEMPLATE_CONFIRM = range(3)

# --- State Keys ---
PARSING_ATTEMPT_ID_KEY = "parsing_attempt_id"
ORIGINAL_PARSED_DATA_KEY = "original_parsed_data"  # Store initial result (with Decimals)
CURRENT_EDIT_DATA_KEY = "current_edit_data"  # Store potentially modified data (with Decimals)
EDITING_FIELD_KEY = "editing_field_key"
RAW_FORWARDED_TEXT_KEY = "raw_forwarded_text"  # Store the original text
ORIGINAL_MESSAGE_ID_KEY = "parsing_review_message_id"  # Store ID of the review card message

# Use management timeout logic for consistency
LAST_ACTIVITY_KEY = "last_activity_management"  # Shared timeout key


def clean_parsing_conversation_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cleans up all keys related to the parsing conversation."""
    keys_to_pop = [
        PARSING_ATTEMPT_ID_KEY, ORIGINAL_PARSED_DATA_KEY, CURRENT_EDIT_DATA_KEY,
        EDITING_FIELD_KEY, RAW_FORWARDED_TEXT_KEY, ORIGINAL_MESSAGE_ID_KEY,
        LAST_ACTIVITY_KEY,
        # Old keys, clear just in case
        'fwd_msg_text', 'pending_trade',
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)

    log.debug("Parsing conversation state cleared for user (if present).")


# --- Entry Point ---
@uow_transaction
@require_active_user  # Ensure user is active before starting
async def forwarded_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """Detects forwarded message, starts parsing, and enters review conversation."""
    message = update.message
    if not message or not message.text or len(message.text) < 10:
        return ConversationHandler.END  # Ignore short/empty/non-text

    # Prevent starting if another known conversation is active using specific keys
    if context.user_data.get(EDITING_FIELD_KEY) \
       or context.user_data.get('rec_creation_draft') \
       or context.user_data.get('awaiting_management_input'):
        log.debug("Forwarded message ignored because another conversation is active.")
        return ConversationHandler.END  # Don't start

    clean_parsing_conversation_state(context)  # Clean state before starting
    update_management_activity(context)  # Start timeout timer

    parsing_service: ParsingService = get_service(context, "parsing_service", ParsingService)
    # Store raw text for later use
    context.user_data[RAW_FORWARDED_TEXT_KEY] = message.text

    # Show initial "Analyzing" message and store its ID
    analyzing_message = await message.reply_text("⏳ Analyzing forwarded message...")
    context.user_data[ORIGINAL_MESSAGE_ID_KEY] = analyzing_message.message_id

    # Get internal DB user ID (already available via db_user from decorators)
    user_db_id = db_user.id

    parsing_result: ParsingResult = await parsing_service.extract_trade_data(message.text, user_db_id)

    # Edit the "Analyzing" message with the result
    if parsing_result.success and parsing_result.data:
        # Store attempt ID and data (with Decimals) for the conversation
        context.user_data[PARSING_ATTEMPT_ID_KEY] = parsing_result.attempt_id
        context.user_data[ORIGINAL_PARSED_DATA_KEY] = parsing_result.data
        # shallow copy is fine if values are primitives/Decimals; make deep copy if nested mutability needed
        context.user_data[CURRENT_EDIT_DATA_KEY] = parsing_result.data.copy()

        keyboard = build_editable_review_card(parsing_result.data)
        await safe_edit_message(
            context.bot, analyzing_message.chat_id, analyzing_message.message_id,
            text="📊 <b>Review Parsed Data</b>\nPlease verify the extracted information:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return AWAIT_REVIEW  # Enter review state
    else:
        # Parsing failed path
        error_msg = getattr(parsing_result, "error_message", None) or "Could not recognize a valid trade signal."
        await safe_edit_message(
            context.bot, analyzing_message.chat_id, analyzing_message.message_id,
            text=f"❌ <b>Analysis Failed</b>\n{error_msg}",
            parse_mode=ParseMode.HTML,
            reply_markup=None  # Remove keyboard
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END  # End conversation on failure


# --- Review State Handlers ---
@uow_transaction
@require_active_user
async def review_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles button presses on the editable review card (Confirm, Edit Field, Cancel)."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
    update_management_activity(context)

    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])

    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    original_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)

    # Check for essential state
    if not current_data or not original_message_id:
        log.warning(f"Parsing review handler called with missing state for user {update.effective_user.id}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Session expired or data lost. Please forward again.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    if action == CallbackAction.CONFIRM.value:
        # --- Final Confirmation Logic ---
        parsing_service: ParsingService = get_service(context, "parsing_service", ParsingService)
        trade_service: TradeService = get_service(context, "trade_service", TradeService)
        attempt_id = context.user_data.get(PARSING_ATTEMPT_ID_KEY)
        original_data = context.user_data.get(ORIGINAL_PARSED_DATA_KEY)
        raw_text = context.user_data.get(RAW_FORWARDED_TEXT_KEY)

        # Check if data was corrected (compare potentially modified current_data with original)
        was_corrected = (original_data != current_data)

        # 1. Save the trade using the potentially corrected data
        result = await trade_service.create_trade_from_forwarding_async(
            user_id=str(db_user.telegram_user_id),
            trade_data=current_data,  # Use the potentially corrected data
            original_text=raw_text,
            db_session=db_session
        )

        # 2. Update attempt record & record correction if needed (run concurrently)
        correction_task = None
        if attempt_id and was_corrected:
            # Ensure record_correction handles Decimals correctly for diff
            correction_task = asyncio.create_task(
                parsing_service.record_correction(attempt_id, current_data, original_data)
            )

        # 3. Respond to user about saving result
        if result.get('success'):
            success_msg = f"✅ <b>Trade #{result['trade_id']}</b> for <b>{result['asset']}</b> tracked successfully!"
            # Edit the original review card message
            await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text=success_msg, reply_markup=None)

            # 4. Ask about saving template IF corrected and originally parsed without template
            if correction_task:
                await correction_task  # Ensure correction is recorded before suggesting

            suggest_template = False
            template_used_initially = False
            if was_corrected and attempt_id:
                try:
                    # Check attempt record for initial template usage
                    repo = parsing_service.parsing_repo_class(db_session)
                    attempt = repo.session.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
                    if attempt and attempt.used_template_id is None:
                        suggest_template = True
                    elif attempt:
                        template_used_initially = True
                except Exception as e:
                    log.error(f"Error checking template usage for suggestion on attempt {attempt_id}: {e}")

            if suggest_template:
                confirm_kb = build_confirmation_keyboard(
                    CallbackNamespace.SAVE_TEMPLATE, attempt_id,
                    confirm_text="💾 Yes, Save Format", cancel_text="🚫 No, Thanks"
                )
                # Send as a new message, replying to the original forwarded message if possible
                reply_to_msg_id = None
                if update.effective_message and update.effective_message.reply_to_message:
                    reply_to_msg_id = update.effective_message.reply_to_message.message_id
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="You corrected the parsed data. Save this message format as a personal template to speed up future analysis?",
                    reply_markup=confirm_kb,
                    reply_to_message_id=reply_to_msg_id
                )
                # Template confirmation is handled separately, end this conversation.
                clean_parsing_conversation_state(context)
                return ConversationHandler.END
            else:
                log.debug(f"Ending parsing conversation. Corrected={was_corrected}, TemplateUsed={template_used_initially}")
                clean_parsing_conversation_state(context)
                return ConversationHandler.END
        else:
            # Trade saving failed
            await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text=f"❌ <b>Error saving trade:</b> {result.get('error', 'Unknown')}", reply_markup=None)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

    elif action == CallbackAction.EDIT_FIELD.value:
        # --- Start Field Correction Flow ---
        if not params:
            log.warning("Edit field callback received without field parameter.")
            return AWAIT_REVIEW  # Stay in review state

        field_to_edit = params[0]
        context.user_data[EDITING_FIELD_KEY] = field_to_edit

        prompts = {
            "asset": "✍️ Send the correct Asset symbol (e.g., BTCUSDT):",
            "side": "↔️ Send the correct Side (LONG or SHORT):",
            "entry": "💰 Send the correct Entry price:",
            "stop_loss": "🛑 Send the correct Stop Loss price:",
            "targets": "🎯 Send the correct Targets (e.g., 61000 62000@50):",
        }
        prompt_text = prompts.get(field_to_edit, f"Send the new value for '{field_to_edit}':")

        # Create a simple cancel button for the input state
        cancel_edit_button = InlineKeyboardButton(
            ButtonTexts.CANCEL + " Edit",
            # Use specific cancel action for edit state
            callback_data=CallbackBuilder.create(CallbackNamespace.FORWARD_PARSE, CallbackAction.CANCEL, "edit")
        )
        input_keyboard = InlineKeyboardMarkup([[cancel_edit_button]])

        # Edit the original review card message to ask for input
        await safe_edit_message(
            context.bot, query.message.chat_id, original_message_id,
            text=f"📝 <b>Editing Field: {field_to_edit.replace('_',' ').title()}</b>\n\n{prompt_text}",
            reply_markup=input_keyboard
        )
        return AWAIT_CORRECTION_VALUE  # Move to state waiting for user's text reply

    elif action == CallbackAction.CANCEL.value:
        # --- Cancel the whole operation ---
        await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text="❌ Operation cancelled.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    # Fallback: Stay in review state if callback is unrecognized
    log.warning(f"Unhandled callback action in review state: {action} from data: {query.data}")
    return AWAIT_REVIEW