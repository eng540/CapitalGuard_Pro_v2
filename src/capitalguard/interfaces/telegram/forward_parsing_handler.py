# --- src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---
"""
Handles the user flow for parsing a forwarded text message, including review and correction.
Uses ConversationHandler for state management during review/edit.
Integrates with ParsingService v4.0 and TradeService v30.9+.
"""

import logging
import time
import json # For diff comparison if needed
from decimal import Decimal
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters,
    ConversationHandler, CommandHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# Infrastructure & Application specific imports
from capitalguard.infrastructure.db.uow import session_scope, uow_transaction
from capitalguard.interfaces.telegram.helpers import get_service, parse_cq_parts
from capitalguard.interfaces.telegram.auth import require_active_user, get_db_user
from capitalguard.application.services.parsing_service import ParsingService, ParsingResult
from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.telegram.keyboards import (
    CallbackBuilder, CallbackNamespace, CallbackAction, build_confirmation_keyboard,
    build_editable_review_card # Import the new keyboard
)
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list # For parsing user corrections
# Import safe_edit_message and timeout helpers (assuming they are now in a shared module or defined here)
from capitalguard.interfaces.telegram.management_handlers import ( # Assuming helpers moved here or a shared util
     safe_edit_message, handle_management_timeout, clean_management_state, update_management_activity, MANAGEMENT_TIMEOUT
)

log = logging.getLogger(__name__)

# --- Conversation States ---
(AWAIT_REVIEW, AWAIT_CORRECTION_FIELD, AWAIT_CORRECTION_VALUE, AWAIT_SAVE_TEMPLATE_CONFIRM) = range(4)

# --- State Keys (Consistent with ParsingService Design) ---
PARSING_ATTEMPT_ID_KEY = "parsing_attempt_id"
ORIGINAL_PARSED_DATA_KEY = "original_parsed_data" # Store initial result (with Decimals)
CURRENT_EDIT_DATA_KEY = "current_edit_data" # Store potentially modified data (with Decimals)
EDITING_FIELD_KEY = "editing_field_key"
RAW_FORWARDED_TEXT_KEY = "raw_forwarded_text" # Store the original text

# Use management timeout logic for consistency
LAST_ACTIVITY_KEY = "last_activity_management"

def clean_parsing_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    """Cleans up all keys related to the parsing conversation."""
    keys_to_pop = [
        PARSING_ATTEMPT_ID_KEY, ORIGINAL_PARSED_DATA_KEY, CURRENT_EDIT_DATA_KEY,
        EDITING_FIELD_KEY, RAW_FORWARDED_TEXT_KEY,
        # Old keys, clear just in case
        'fwd_msg_text', 'pending_trade',
        # Management keys if potentially overlapping
        LAST_ACTIVITY_KEY, 'awaiting_management_input', 'pending_management_change'
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    log.debug("Parsing conversation state cleared.")

# --- Entry Point ---
# Wrapped with UOW to get user ID safely at the start
@uow_transaction
@require_active_user # Ensure user is active before starting
async def forwarded_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """Detects forwarded message, starts parsing, and enters review conversation."""
    message = update.message
    if not message or not message.text or len(message.text) < 10: return ConversationHandler.END # Ignore short/empty messages
    # Prevent starting if another known conversation is active
    # A more robust check might involve inspecting context.handlers
    if context.user_data.get(EDITING_FIELD_KEY) or context.user_data.get('rec_creation_draft'):
         log.debug("Forwarded message ignored because another conversation seems active.")
         return ConversationHandler.END # Don't start if another conv seems active

    clean_parsing_conversation_state(context) # Clean state before starting
    update_management_activity(context) # Start timeout timer

    parsing_service = get_service(context, "parsing_service", ParsingService)
    # Store raw text for later use (saving trade, suggesting template)
    context.user_data[RAW_FORWARDED_TEXT_KEY] = message.text

    # Show initial "Analyzing" message
    sent_message = await message.reply_text("⏳ Analyzing forwarded message...")

    # Get internal DB user ID (already available via db_user from decorators)
    user_db_id = db_user.id

    parsing_result: ParsingResult = await parsing_service.extract_trade_data(message.text, user_db_id)

    if parsing_result.success and parsing_result.data:
        # Store attempt ID and data (with Decimals) for the conversation
        context.user_data[PARSING_ATTEMPT_ID_KEY] = parsing_result.attempt_id
        context.user_data[ORIGINAL_PARSED_DATA_KEY] = parsing_result.data
        context.user_data[CURRENT_EDIT_DATA_KEY] = parsing_result.data.copy() # Start edits from original

        keyboard = build_editable_review_card(parsing_result.data)
        await context.bot.edit_message_text(
            chat_id=sent_message.chat_id,
            message_id=sent_message.message_id,
            text="📊 **Review Parsed Data**\nPlease verify the extracted information:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return AWAIT_REVIEW
    else:
        # Parsing failed path
        error_msg = parsing_result.error_message or "Could not recognize a valid trade signal."
        # Optionally suggest manual entry using interactive builder?
        # For now, just report failure.
        await context.bot.edit_message_text(
            chat_id=sent_message.chat_id,
            message_id=sent_message.message_id,
            text=f"❌ **Analysis Failed**\n{error_msg}",
            parse_mode=ParseMode.HTML
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

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
    if not current_data:
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

        # Check if data was corrected
        was_corrected = (original_data != current_data)

        # 1. Save the trade using the potentially corrected data
        result = await trade_service.create_trade_from_forwarding_async(
            user_id=str(db_user.telegram_user_id),
            trade_data=current_data, # Use the potentially corrected data
            original_text=raw_text, # Pass original text
            db_session=db_session
        )

        # 2. Update attempt record & record correction if needed in parallel
        correction_task = None
        if attempt_id and was_corrected:
             correction_task = parsing_service.record_correction(attempt_id, current_data, original_data)
             # Don't await here, let it run in background

        # 3. Respond to user
        if result.get('success'):
            success_msg = f"✅ **Trade #{result['trade_id']}** for **{result['asset']}** tracked successfully!"
            await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=success_msg, reply_markup=None)

            # 4. Ask about saving template IF corrected and originally parsed without template
            # Run suggestion logic after confirming save & recording correction
            if correction_task: await correction_task # Ensure correction is recorded before suggesting template

            suggest_template = False
            if was_corrected and attempt_id:
                 # Check if original parse used a template
                 try:
                      with session_scope() as s:
                           attempt = s.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
                           if attempt and attempt.used_template_id is None:
                                suggest_template = True
                 except Exception as e:
                      log.error(f"Error checking template usage for suggestion on attempt {attempt_id}: {e}")

            if suggest_template:
                 # Generate confirmation keyboard for saving template
                 confirm_kb = build_confirmation_keyboard(
                      CallbackNamespace.SAVE_TEMPLATE, # Use specific namespace
                      attempt_id,
                      confirm_text="💾 Yes, Save Format",
                      cancel_text="🚫 No, Thanks"
                 )
                 await query.message.reply_text( # Send as new message
                      "You corrected the parsed data. Would you like to save this message format as a personal template to speed up future analysis?",
                      reply_markup=confirm_kb
                 )
                 clean_parsing_conversation_state(context) # Clean base state
                 # No need to return a state here, template confirmation is separate handler
                 return ConversationHandler.END # End main parsing flow
            else:
                 clean_parsing_conversation_state(context) # Clean state on success
                 return ConversationHandler.END
        else:
            # Trade saving failed
            await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=f"❌ **Error saving trade:** {result.get('error', 'Unknown')}", reply_markup=None)
            # Keep state for potential retry? For now, end.
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

    elif action == CallbackAction.EDIT_FIELD.value:
        # --- Start Field Correction Flow ---
        if not params:
            log.warning("Edit field callback received without field parameter.")
            return AWAIT_REVIEW # Stay in review state

        field_to_edit = params[0]
        context.user_data[EDITING_FIELD_KEY] = field_to_edit

        # Define user-friendly prompts
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
            "❌ Cancel Edit",
            callback_data=CallbackBuilder.create(CallbackNamespace.FORWARD_PARSE, CallbackAction.CANCEL, "edit") # Cancel edit action
        )
        input_keyboard = InlineKeyboardMarkup([[cancel_edit_button]])

        # Edit the original review card message to ask for input
        await safe_edit_message(
            context.bot, query.message.chat_id, query.message.message_id,
            text=f"📝 **Editing Field: {field_to_edit.replace('_',' ').title()}**\n\n{prompt_text}",
            reply_markup=input_keyboard
        )
        return AWAIT_CORRECTION_VALUE # Move to state waiting for user's text reply

    elif action == CallbackAction.CANCEL.value:
        # --- Cancel the whole operation ---
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Operation cancelled.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    # Fallback: Stay in review state if callback is unrecognized within this handler
    log.warning(f"Unhandled callback action in review state: {action}")
    return AWAIT_REVIEW

# --- Correction Input Handler ---
# Needs UOW because it might need to re-validate using TradeService
@uow_transaction
@require_active_user
async def correction_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """Handles the user's text reply with the corrected value for a specific field."""
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)

    field_to_edit = context.user_data.get(EDITING_FIELD_KEY)
    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    user_input = update.message.text.strip() if update.message and update.message.text else ""

    # Get original message ID to edit later
    # This assumes the prompt was edited in place from the review card
    original_message_id = update.message.reply_to_message.message_id if update.message.reply_to_message else None

    # Try deleting user's input message
    try: await update.message.delete()
    except Exception: pass

    if not field_to_edit or not current_data or not original_message_id:
        log.warning("Correction value handler called with invalid state.")
        # Attempt to recover by sending a new message if original edit target is lost
        await update.effective_chat.send_message("❌ Session error during correction. Please start over by forwarding the message again.")
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    try:
        # Validate and update the specific field in current_data
        validated = False
        temp_data = current_data.copy() # Validate on a copy

        if field_to_edit == "asset":
            # Basic validation: Non-empty, uppercase
            new_asset = user_input.upper()
            if not new_asset: raise ValueError("Asset symbol cannot be empty.")
            temp_data['asset'] = new_asset
            validated = True
        elif field_to_edit == "side":
            side_upper = user_input.upper()
            if side_upper in ["LONG", "SHORT"]:
                temp_data['side'] = side_upper
                validated = True
            else: raise ValueError("Side must be LONG or SHORT.")
        elif field_to_edit in ["entry", "stop_loss"]:
            price = parse_number(user_input) # Returns Decimal or None
            if price is None: raise ValueError("Invalid number format for price.")
            temp_data[field_to_edit] = price
            validated = True
        elif field_to_edit == "targets":
            targets = parse_targets_list(user_input.split()) # Returns List[Dict] with Decimals
            if not targets: raise ValueError("Invalid targets format or no valid targets found.")
            temp_data['targets'] = targets
            validated = True

        # Perform full validation using TradeService logic on the temporary data
        if validated:
            trade_service = get_service(context, "trade_service", TradeService)
            # Ensure all required fields exist for validation
            temp_data.setdefault('asset', current_data.get('asset'))
            temp_data.setdefault('side', current_data.get('side'))
            temp_data.setdefault('entry', current_data.get('entry'))
            temp_data.setdefault('stop_loss', current_data.get('stop_loss'))
            temp_data.setdefault('targets', current_data.get('targets'))
            # Call validation (might raise ValueError)
            trade_service._validate_recommendation_data(
                 temp_data['side'], temp_data['entry'], temp_data['stop_loss'], temp_data['targets']
            )
            # If validation passes, update the actual current_data
            current_data[field_to_edit] = temp_data[field_to_edit]
            log.info(f"Field '{field_to_edit}' corrected successfully by user {update.effective_user.id}")

            context.user_data.pop(EDITING_FIELD_KEY, None) # Clear editing field state

            # Re-render the review card with updated data
            keyboard = build_editable_review_card(current_data)
            await safe_edit_message(
                 context.bot, update.effective_chat.id, original_message_id,
                 text="✅ Value updated. Please review again:",
                 reply_markup=keyboard,
                 parse_mode=ParseMode.HTML
            )
            return AWAIT_REVIEW # Go back to review state
        else:
            # Should not happen if individual parsing above is correct
             raise ValueError("Internal validation failed before full check.")

    except ValueError as e:
        log.warning(f"Invalid correction input by user {update.effective_user.id} for field '{field_to_edit}': {e}")
        # Re-prompt, keeping state
        cancel_edit_button = InlineKeyboardButton("❌ Cancel Edit", callback_data=CallbackBuilder.create(CallbackNamespace.FORWARD_PARSE, CallbackAction.CANCEL, "edit"))
        await context.bot.send_message( # Send as new message to avoid editing issues
            chat_id=update.effective_chat.id,
            text=f"⚠️ Invalid input: {e}\nPlease try again for **{field_to_edit.replace('_',' ').title()}** or cancel:",
            reply_markup=InlineKeyboardMarkup([[cancel_edit_button]]),
            parse_mode=ParseMode.MARKDOWN_V2 # Use Markdown if preferred
        )
        return AWAIT_CORRECTION_VALUE # Stay in value input state

    except Exception as e:
        log.error(f"Error handling correction for {field_to_edit} by user {update.effective_user.id}: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ An unexpected error occurred during correction. Please try cancelling and starting over."
        )
        # Go back to review state on unexpected error to allow cancellation
        keyboard = build_editable_review_card(current_data)
        await safe_edit_message(context.bot, update.effective_chat.id, original_message_id, text="Error during edit. Review:", reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return AWAIT_REVIEW


# --- Save Template Confirmation Handler ---
@uow_transaction
@require_active_user
async def save_template_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles Yes/No response for saving a template after correction."""
    query = update.callback_query
    await query.answer()
    # No timeout check here, it's a quick confirmation outside main flow
    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    attempt_id = int(params[0]) if params and params[0].isdigit() else None

    # Make sure message is not edited again if user clicks multiple times
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=query.message.text, reply_markup=None) # Remove keyboard

    if action == CallbackAction.CONFIRM.value and attempt_id:
        parsing_service = get_service(context, "parsing_service", ParsingService)
        # --- Basic Template Saving Logic (MVP) ---
        try:
            attempt = db_session.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
            if attempt and attempt.was_corrected and attempt.user_id == db_user.id: # Security check
                # MVP: Save raw content + diff for manual review by admin/analyst later
                # A more advanced version would generate a Regex pattern here.
                pattern_placeholder = f"# REVIEW NEEDED: Attempt {attempt_id}\n# User Correction Diff:\n# {json.dumps(attempt.corrections_diff, indent=2)}\n\n# Original Content:\n{attempt.raw_content}"
                template_name = f"User {db_user.id} Suggestion {time.strftime('%Y%m%d%H%M')}"

                repo = parsing_service.parsing_repo_class(db_session)
                repo.add_template(
                    name=template_name,
                    pattern_type='regex_manual_review', # Mark for review
                    pattern_value=pattern_placeholder,
                    analyst_id=db_user.id, # Link to user who corrected
                    is_public=False, # Must be reviewed first
                    stats={"source_attempt_id": attempt_id}
                )
                await query.message.reply_text("✅ Template format submitted for review. It will become active after approval.")
            else:
                await query.message.reply_text("ℹ️ Template data mismatch or already processed.")
        except Exception as e:
            log.error(f"Error saving template from attempt {attempt_id}: {e}", exc_info=True)
            await query.message.reply_text("❌ An error occurred while submitting the template.")
    else: # Cancelled or invalid data
        await query.message.reply_text("ℹ️ Template format not saved.")

    # No state change needed, just end interaction
    return ConversationHandler.END


# --- General Cancel / Fallback ---
async def cancel_parsing_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Generic cancel handler for the parsing conversation states."""
    clean_parsing_conversation_state(context)
    message_text = "❌ Operation cancelled."
    target_chat_id = update.effective_chat.id
    target_message_id = None

    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.message:
             target_message_id = update.callback_query.message.message_id
             # Try to edit the original message if possible
             await safe_edit_message(context.bot, target_chat_id, target_message_id, text=message_text, reply_markup=None)
             return ConversationHandler.END

    # Fallback to sending a new message if editing fails or not a callback
    await context.bot.send_message(chat_id=target_chat_id, text=message_text)
    return ConversationHandler.END

# --- Registration ---
def register_forward_parsing_handlers(app: Application):
    """Registers the conversation handler for forward parsing and review."""
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(
            filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            forwarded_message_handler
        )],
        states={
            AWAIT_REVIEW: [CallbackQueryHandler(
                review_callback_handler,
                pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:" # Handles Confirm, Edit, Cancel
            )],
            AWAIT_CORRECTION_VALUE: [MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, # Only allow text replies
                correction_value_handler
            )],
            # AWAIT_SAVE_TEMPLATE_CONFIRM is handled by a separate handler below
        },
        fallbacks=[
            CommandHandler("cancel", cancel_parsing_conversation),
            # Cancel button during correction input
            CallbackQueryHandler(cancel_parsing_conversation, pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:{CallbackAction.CANCEL.value}:edit"),
            # Catch-all for unexpected callbacks/messages during conversation
            MessageHandler(filters.ALL & filters.ChatType.PRIVATE, cancel_parsing_conversation),
            CallbackQueryHandler(cancel_parsing_conversation, pattern="^.*")
        ],
        name="forward_parsing_conversation",
        per_user=True, per_chat=True,
        persistent=False, # Use RedisPersistence via bootstrap_app
        # conversation_timeout=MANAGEMENT_TIMEOUT # Use shared timeout logic
    )
    # Use group=1 to ensure it runs after command handlers (group=0)
    app.add_handler(conv_handler, group=1)

    # Add the separate handler for template saving confirmation (outside the conversation states)
    app.add_handler(CallbackQueryHandler(save_template_confirm_handler, pattern=f"^{CallbackNamespace.SAVE_TEMPLATE.value}:"), group=1)

# --- END of forward parsing handler ---