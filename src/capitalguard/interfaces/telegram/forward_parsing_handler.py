# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---
# --- src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---
"""
Handles the user flow for parsing a forwarded text message, including review and correction (v2.0.1 - Warning Suppress).
Uses ConversationHandler for state management during review/edit.
Integrates with ParsingService v4.1+ and TradeService v31.0+.
Includes logic for suggesting template saves.
✅ FIX: Added `per_message=False` to ConversationHandler registration to suppress PTBUserWarning noise in logs.
"""

import logging
import time
import json # For diff comparison if needed
import asyncio
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
    build_editable_review_card, ButtonTexts # Import ButtonTexts
)
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list
# Import safe_edit_message and timeout helpers from management_handlers
from capitalguard.interfaces.telegram.management_handlers import (
     safe_edit_message, handle_management_timeout, update_management_activity, MANAGEMENT_TIMEOUT
)
# Import repository for direct DB checks if needed (e.g., checking attempt state)
from capitalguard.infrastructure.db.repository import ParsingRepository
from capitalguard.infrastructure.db.models import ParsingAttempt

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors") # Error logger

# --- Conversation States ---
(AWAIT_REVIEW, AWAIT_CORRECTION_VALUE, AWAIT_SAVE_TEMPLATE_CONFIRM) = range(3) # Simplified states

# --- State Keys ---
PARSING_ATTEMPT_ID_KEY = "parsing_attempt_id"
ORIGINAL_PARSED_DATA_KEY = "original_parsed_data" # Store initial result (with Decimals)
CURRENT_EDIT_DATA_KEY = "current_edit_data" # Store potentially modified data (with Decimals)
EDITING_FIELD_KEY = "editing_field_key"
RAW_FORWARDED_TEXT_KEY = "raw_forwarded_text" # Store the original text
ORIGINAL_MESSAGE_ID_KEY = "parsing_review_message_id" # Store ID of the review card message

# Use management timeout logic for consistency
LAST_ACTIVITY_KEY = "last_activity_management" # Shared timeout key

def clean_parsing_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    """Cleans up all keys related to the parsing conversation."""
    keys_to_pop = [
        PARSING_ATTEMPT_ID_KEY, ORIGINAL_PARSED_DATA_KEY, CURRENT_EDIT_DATA_KEY,
        EDITING_FIELD_KEY, RAW_FORWARDED_TEXT_KEY, ORIGINAL_MESSAGE_ID_KEY,
        LAST_ACTIVITY_KEY, # Clean shared timeout key too
        # Old keys, clear just in case
        'fwd_msg_text', 'pending_trade',
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    
    log.debug(f"Parsing conversation state cleared for user {context._user_id}.")

# --- Entry Point ---
@uow_transaction
@require_active_user # Ensure user is active before starting
async def forwarded_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """Detects forwarded message, starts parsing, and enters review conversation."""
    message = update.message
    if not message or not message.text or len(message.text) < 10: return ConversationHandler.END # Ignore short/empty/non-text

    # Prevent starting if another known conversation is active using specific keys
    if context.user_data.get(EDITING_FIELD_KEY) \
       or context.user_data.get('rec_creation_draft') \
       or context.user_data.get('awaiting_management_input'): # Check management state key
         log.debug("Forwarded message ignored because another conversation is active.")
         # Maybe notify user? "Please finish your current action first."
         return ConversationHandler.END # Don't start

    clean_parsing_conversation_state(context) # Clean state before starting
    update_management_activity(context) # Start timeout timer

    parsing_service = get_service(context, "parsing_service", ParsingService)
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
        context.user_data[CURRENT_EDIT_DATA_KEY] = parsing_result.data.copy() # Start edits from original

        keyboard = build_editable_review_card(parsing_result.data)
        await safe_edit_message(
            context.bot, analyzing_message.chat_id, analyzing_message.message_id,
            text="📊 **Review Parsed Data**\nPlease verify the extracted information:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return AWAIT_REVIEW # Enter review state
    else:
        # Parsing failed path
        error_msg = parsing_result.error_message or "Could not recognize a valid trade signal."
        # Optionally suggest manual entry using interactive builder?
        # For now, just report failure.
        await safe_edit_message(
             context.bot, analyzing_message.chat_id, analyzing_message.message_id,
             text=f"❌ **Analysis Failed**\n{error_msg}",
             parse_mode=ParseMode.HTML,
             reply_markup=None # Remove keyboard
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END # End conversation on failure

# --- Review State Handlers ---
@uow_transaction
@require_active_user
async def review_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles button presses on the editable review card (Confirm, Edit Field, Cancel)."""
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return ConversationHandler.END
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
        parsing_service = get_service(context, "parsing_service", ParsingService)
        trade_service = get_service(context, "trade_service", TradeService)
        attempt_id = context.user_data.get(PARSING_ATTEMPT_ID_KEY)
        original_data = context.user_data.get(ORIGINAL_PARSED_DATA_KEY)
        raw_text = context.user_data.get(RAW_FORWARDED_TEXT_KEY)

        # Check if data was corrected (compare potentially modified current_data with original)
        was_corrected = (original_data != current_data)

        # 1. Save the trade using the potentially corrected data
        result = await trade_service.create_trade_from_forwarding_async(
            user_id=str(db_user.telegram_user_id),
            trade_data=current_data, # Use the potentially corrected data
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
            success_msg = f"✅ **Trade #{result['trade_id']}** for **{result['asset']}** tracked successfully!"
            # Edit the original review card message
            await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text=success_msg, reply_markup=None)

            # 4. Ask about saving template IF corrected and originally parsed without template
            if correction_task: await correction_task # Ensure correction is recorded before suggesting

            suggest_template = False
            template_used_initially = False
            if was_corrected and attempt_id:
                 try:
                      # Check attempt record for initial template usage
                      # No need for separate session if already in UOW transaction
                      repo = parsing_service.parsing_repo_class(db_session)
                      attempt = repo.session.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
                      if attempt and attempt.used_template_id is None:
                           suggest_template = True
                      elif attempt:
                           template_used_initially = True # Flag if correction happened despite template
                 except Exception as e:
                      log.error(f"Error checking template usage for suggestion on attempt {attempt_id}: {e}")

            if suggest_template:
                 confirm_kb = build_confirmation_keyboard(
                      CallbackNamespace.SAVE_TEMPLATE, attempt_id,
                      confirm_text="💾 Yes, Save Format", cancel_text="🚫 No, Thanks"
                 )
                 # Send as a new message, replying to the original forwarded message if possible
                 reply_to_msg_id = update.effective_message.reply_to_message.message_id if update.effective_message and update.effective_message.reply_to_message else None
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
                 # If no suggestion needed (or correction happened with template), just end.
                 log.debug(f"Ending parsing conversation. Corrected={was_corrected}, TemplateUsed={template_used_initially}")
                 clean_parsing_conversation_state(context)
                 return ConversationHandler.END
        else:
            # Trade saving failed
            await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text=f"❌ **Error saving trade:** {result.get('error', 'Unknown')}", reply_markup=None)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

    elif action == CallbackAction.EDIT_FIELD.value:
        # --- Start Field Correction Flow ---
        if not params:
            log.warning("Edit field callback received without field parameter.")
            return AWAIT_REVIEW # Stay in review state

        field_to_edit = params[0]
        context.user_data[EDITING_FIELD_KEY] = field_to_edit

        prompts = {
            "asset": "✍️ Send the correct Asset symbol (e.g., BTCUSDT):",
            "side": "↔️ Send the correct Side (LONG or SHORT):",
            "entry": "💰 Send the correct Entry price:",
            "stop_loss": "🛑 Send the correct Stop Loss price:",
            "targets": "🎯 Send the correct Targets (e.g., 61k 62k@50):",
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
            text=f"📝 **Editing Field: {field_to_edit.replace('_',' ').title()}**\n\n{prompt_text}",
            reply_markup=input_keyboard
        )
        return AWAIT_CORRECTION_VALUE # Move to state waiting for user's text reply

    elif action == CallbackAction.CANCEL.value:
        # --- Cancel the whole operation ---
        await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text="❌ Operation cancelled.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    # Fallback: Stay in review state if callback is unrecognized
    log.warning(f"Unhandled callback action in review state: {action} from data: {query.data}")
    return AWAIT_REVIEW

# --- Correction Input Handler ---
# Needs UOW because it calls TradeService validation which might need DB context later
@uow_transaction
@require_active_user
async def correction_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """Handles the user's text reply with the corrected value for a specific field."""
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)

    field_to_edit = context.user_data.get(EDITING_FIELD_KEY)
    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    # Use original_message_id stored in context
    original_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)
    user_input = update.message.text.strip() if update.message.text else ""

    # Try deleting user's input message immediately
    try: await update.message.delete()
    except Exception: log.debug("Could not delete user correction message.")

    # Validate state
    if not field_to_edit or not current_data or not original_message_id:
        log.warning(f"Correction value handler called with invalid state for user {update.effective_user.id}.")
        await update.effective_chat.send_message("❌ Session error during correction. Please start over.")
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    try:
        # --- Validate and update the specific field in current_data ---
        validated = False
        temp_data = current_data.copy() # Validate on a copy before modifying state

        if field_to_edit == "asset":
            new_asset = user_input.upper()
            if not new_asset: raise ValueError("Asset symbol cannot be empty.")
            # TODO: Add MarketDataService validation if desired
            temp_data['asset'] = new_asset
            validated = True
        elif field_to_edit == "side":
            side_upper = user_input.upper()
            if side_upper in ["LONG", "SHORT"]: temp_data['side'] = side_upper; validated = True
            else: raise ValueError("Side must be LONG or SHORT.")
        elif field_to_edit in ["entry", "stop_loss"]:
            price = parse_number(user_input) # Returns Decimal or None
            if price is None: raise ValueError("Invalid number format for price.")
            temp_data[field_to_edit] = price
            validated = True
        elif field_to_edit == "targets":
            # parse_targets_list expects list of strings
            targets = parse_targets_list(user_input.split()) # Returns List[Dict] with Decimals
            if not targets: raise ValueError("Invalid targets format or no valid targets found.")
            temp_data['targets'] = targets
            validated = True

        # --- Perform full validation on temp_data ---
        if validated:
            trade_service = get_service(context, "trade_service", TradeService)
            # Ensure all required fields exist for validation in temp_data
            temp_data.setdefault('asset', current_data.get('asset'))
            temp_data.setdefault('side', current_data.get('side'))
            temp_data.setdefault('entry', current_data.get('entry'))
            temp_data.setdefault('stop_loss', current_data.get('stop_loss'))
            temp_data.setdefault('targets', current_data.get('targets'))

            # Call validation using Decimals
            trade_service._validate_recommendation_data(
                 temp_data['side'], temp_data['entry'], temp_data['stop_loss'], temp_data['targets']
            )

            # --- If validation passes, update actual context state ---
            current_data[field_to_edit] = temp_data[field_to_edit]
            log.info(f"Field '{field_to_edit}' corrected successfully by user {update.effective_user.id}")
            context.user_data.pop(EDITING_FIELD_KEY, None) # Clear editing field state

            # --- Re-render the review card with updated data ---
            keyboard = build_editable_review_card(current_data)
            await safe_edit_message(
                 context.bot, update.effective_chat.id, original_message_id,
                 text="✅ Value updated. Please review again:",
                 reply_markup=keyboard,
                 parse_mode=ParseMode.HTML
            )
            return AWAIT_REVIEW # Go back to review state
        else:
             # Should not happen if individual parsing is correct
             raise ValueError(f"Internal validation failed for field '{field_to_edit}'.")

    except ValueError as e:
        log.warning(f"Invalid correction input by user {update.effective_user.id} for field '{field_to_edit}': {e}")
        # --- Re-prompt, keeping state ---
        cancel_edit_button = InlineKeyboardButton(
            ButtonTexts.CANCEL + " Edit",
            callback_data=CallbackBuilder.create(CallbackNamespace.FORWARD_PARSE, CallbackAction.CANCEL, "edit")
        )
        # Re-edit the original message asking for input again
        await safe_edit_message(
            context.bot, update.effective_chat.id, original_message_id,
            text=f"⚠️ **Invalid Input:** {e}\nPlease try again for **{field_to_edit.replace('_',' ').title()}** or cancel:",
            reply_markup=InlineKeyboardMarkup([[cancel_edit_button]]),
            parse_mode=ParseMode.HTML # Or Markdown
        )
        return AWAIT_CORRECTION_VALUE # Stay in value input state

    except Exception as e:
        log.error(f"Error handling correction for {field_to_edit} by user {update.effective_user.id}: {e}", exc_info=True)
        await context.bot.send_message( # Send new message on unexpected error
            chat_id=update.effective_chat.id,
            text="❌ An unexpected error occurred during correction. Operation cancelled."
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END


# --- Save Template Confirmation Handler (Standalone - Outside Conversation) ---
@uow_transaction
@require_active_user
async def save_template_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles Yes/No response for saving a template after correction."""
    query = update.callback_query
    await query.answer()
    # No timeout check needed here, it's a simple confirmation

    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    attempt_id = int(params[0]) if params and params[0].isdigit() else None

    # Remove keyboard from the confirmation message
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=query.message.text_html, reply_markup=None)

    if action == CallbackAction.CONFIRM.value and attempt_id:
        parsing_service = get_service(context, "parsing_service", ParsingService) # Get service
        # --- Basic Template Saving Logic (MVP) ---
        try:
            repo = parsing_service.parsing_repo_class(db_session) # Use repo class from service
            attempt = repo.session.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
            # Ensure attempt exists, was corrected, belongs to user, and wasn't from a template initially
            if attempt and attempt.was_corrected and attempt.user_id == db_user.id and attempt.used_template_id is None:
                # MVP: Save raw content + diff for manual review by admin/analyst later
                pattern_placeholder = (f"# REVIEW NEEDED: Attempt {attempt_id}\n"
                                     f"# User Correction Diff:\n# {json.dumps(attempt.corrections_diff, indent=2)}\n\n"
                                     f"# Original Content:\n{attempt.raw_content}")
                template_name = f"User {db_user.telegram_user_id} Suggestion {time.strftime('%Y%m%d%H%M%S')}" # More unique name

                # Use repo instance within session
                new_template = repo.add_template(
                    name=template_name,
                    pattern_type='regex_manual_review', # Mark for review
                    pattern_value=pattern_placeholder,
                    analyst_id=db_user.id, # Link to user who corrected
                    is_public=False, # Must be reviewed first
                    stats={"source_attempt_id": attempt_id}
                )
                db_session.commit() # Commit the new template
                await query.message.reply_text(f"✅ Template suggestion (ID: {new_template.id}) submitted for review.")
            else:
                await query.message.reply_text("ℹ️ Template suggestion invalid or already processed.")
        except Exception as e:
            log.error(f"Error saving template suggestion from attempt {attempt_id}: {e}", exc_info=True)
            db_session.rollback() # Rollback on error
            await query.message.reply_text("❌ Error submitting template suggestion.")
    else: # Cancelled or invalid data
        await query.message.reply_text("ℹ️ Template suggestion discarded.")

    # This handler is standalone, no state change needed
    return ConversationHandler.END # Use END to signify handler completion


# --- General Cancel / Fallback ---
async def cancel_parsing_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Generic cancel handler for the parsing conversation states."""
    message_text = "❌ Operation cancelled."
    target_chat_id = update.effective_chat.id
    target_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY) # Try to use stored ID

    if update.callback_query:
        await update.callback_query.answer()
        # Use stored ID if callback message ID is different (e.g., from cancel button in prompt)
        if not target_message_id and update.callback_query.message:
            target_message_id = update.callback_query.message.message_id

    clean_parsing_conversation_state(context) # Clean state AFTER getting IDs

    if target_message_id:
        # Try to edit the original message
        await safe_edit_message(context.bot, target_chat_id, target_message_id, text=message_text, reply_markup=None)
    elif update.message: # Fallback if editing fails or not a callback
        await update.message.reply_text(message_text)
    else: # Absolute fallback
        await context.bot.send_message(chat_id=target_chat_id, text=message_text)

    return ConversationHandler.END


# --- Registration ---
def register_forward_parsing_handlers(app: Application):
    """Registers the conversation handler for forward parsing and review."""
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(
            # More specific filter: Forwarded text, not command, private chat
            filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            forwarded_message_handler
        )],
        states={
            AWAIT_REVIEW: [CallbackQueryHandler(
                review_callback_handler,
                # Pattern matches Confirm, Edit Field, Cancel actions in FORWARD_PARSE namespace
                pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:(?:{CallbackAction.CONFIRM.value}|{CallbackAction.EDIT_FIELD.value}|{CallbackAction.CANCEL.value}):"
            )],
            AWAIT_CORRECTION_VALUE: [MessageHandler(
                # Only allow text replies in private chat for correction
                filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                correction_value_handler
            )],
            # AWAIT_SAVE_TEMPLATE_CONFIRM is handled by a separate handler below
        },
        fallbacks=[
            CommandHandler("cancel", cancel_parsing_conversation),
            # Specific cancel button during correction input state
            CallbackQueryHandler(cancel_parsing_conversation, pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:{CallbackAction.CANCEL.value}:edit"),
            # Catch-all for unexpected callbacks/messages during conversation
            CallbackQueryHandler(cancel_parsing_conversation, pattern="^.*"), # Catch any stray callback
            MessageHandler(filters.ALL & filters.ChatType.PRIVATE, cancel_parsing_conversation), # Catch any stray message
        ],
        name="forward_parsing_conversation",
        per_user=True, per_chat=True,
        persistent=False, # Rely on RedisPersistence configured at app level
        conversation_timeout=MANAGEMENT_TIMEOUT, # Use shared timeout
        per_message=False # ✅ FIX: Suppress warning
    )
    # Use group=1 to ensure it runs after command handlers (group=0)
    app.add_handler(conv_handler, group=1)

    # Add the separate handler for template saving confirmation (outside the conversation states)
    # Needs to run after conversations (group 1 or higher)
    app.add_handler(CallbackQueryHandler(
        save_template_confirm_handler,
        pattern=f"^{CallbackNamespace.SAVE_TEMPLATE.value}:" # Handles Confirm/Cancel
    ), group=1)

# --- END of forward parsing handler ---