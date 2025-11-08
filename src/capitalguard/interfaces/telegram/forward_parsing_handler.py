# --- src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---
"""
Handles the user flow for parsing a forwarded text message (v3.0.2 - Smart Correction).
✅ HOTFIX: Updated correction_value_handler to use a smarter regex tokenizer.
This correctly parses complex user inputs like "0.105 - 0.112 - 0.15 (25% each)".
"""

import logging
import time
import json 
import asyncio
import httpx 
import os 
import re # ✅ NEW: Import Regex library
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
from capitalguard.application.services.parsing_service import ParsingResult 
from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.telegram.keyboards import (
    CallbackBuilder, CallbackNamespace, CallbackAction, build_confirmation_keyboard,
    build_editable_review_card, ButtonTexts
)
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list
from capitalguard.interfaces.telegram.management_handlers import (
     safe_edit_message, handle_management_timeout, update_management_activity, MANAGEMENT_TIMEOUT
)
from capitalguard.infrastructure.db.repository import ParsingRepository 
from capitalguard.infrastructure.db.models import ParsingAttempt 

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- Conversation States ---
(AWAIT_REVIEW, AWAIT_CORRECTION_VALUE, AWAIT_SAVE_TEMPLATE_CONFIRM) = range(3)

# --- State Keys ---
PARSING_ATTEMPT_ID_KEY = "parsing_attempt_id"
ORIGINAL_PARSED_DATA_KEY = "original_parsed_data"
CURRENT_EDIT_DATA_KEY = "current_edit_data"
EDITING_FIELD_KEY = "editing_field_key"
RAW_FORWARDED_TEXT_KEY = "raw_forwarded_text"
ORIGINAL_MESSAGE_ID_KEY = "parsing_review_message_id"
LAST_ACTIVITY_KEY = "last_activity_management"
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL")

if not AI_SERVICE_URL:
    log.critical("AI_SERVICE_URL environment variable is not set! Forward parsing will fail.")

def clean_parsing_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    """Cleans up all keys related to the parsing conversation."""
    keys_to_pop = [
        PARSING_ATTEMPT_ID_KEY, ORIGINAL_PARSED_DATA_KEY, CURRENT_EDIT_DATA_KEY,
        EDITING_FIELD_KEY, RAW_FORWARDED_TEXT_KEY, ORIGINAL_MESSAGE_ID_KEY,
        LAST_ACTIVITY_KEY, 'fwd_msg_text', 'pending_trade',
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    log.debug(f"Parsing conversation state cleared for user {context._user_id}.")

# --- Entry Point ---
@uow_transaction
@require_active_user 
async def forwarded_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    message = update.message
    if not message or not message.text or len(message.text) < 10: return ConversationHandler.END

    if context.user_data.get(EDITING_FIELD_KEY) \
       or context.user_data.get('rec_creation_draft') \
       or context.user_data.get('awaiting_management_input'):
         log.debug("Forwarded message ignored because another conversation is active.")
         return ConversationHandler.END

    clean_parsing_conversation_state(context)
    update_management_activity(context)

    if not AI_SERVICE_URL:
        log.error("Forwarded message received, but AI_SERVICE_URL is not configured.")
        await message.reply_text("❌ Feature unavailable: The analysis service is not configured.")
        return ConversationHandler.END

    context.user_data[RAW_FORWARDED_TEXT_KEY] = message.text
    analyzing_message = await message.reply_text("⏳ Analyzing forwarded message...")
    context.user_data[ORIGINAL_MESSAGE_ID_KEY] = analyzing_message.message_id
    user_db_id = db_user.id
    parsing_result: ParsingResult 

    try:
        log.debug(f"Calling AI Service at {AI_SERVICE_URL} for user {user_db_id}")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                AI_SERVICE_URL,
                json={"text": message.text, "user_id": user_db_id},
                timeout=20.0
            )
            
            if response.status_code >= 400:
                log.error(f"AI Service returned HTTP {response.status_code}: {response.text[:200]}")
                error_detail = response.json().get("detail", "Analysis service failed.")
                parsing_result = ParsingResult(success=False, error_message=f"Error {response.status_code}: {error_detail}")
            else:
                json_data = response.json()
                hydrated_data = None
                if json_data.get("data"):
                    try:
                        raw = json_data["data"]
                        hydrated_data = {
                            "asset": raw.get("asset"),
                            "side": raw.get("side"),
                            "entry": parse_number(raw.get("entry")),
                            "stop_loss": parse_number(raw.get("stop_loss")),
                            "targets": parse_targets_list([f"{t.get('price')}@{t.get('close_percent')}" for t in raw.get("targets", [])])
                        }
                    except Exception as e:
                        log.error(f"Failed to re-hydrate JSON from AI service: {e}")
                        parsing_result = ParsingResult(success=False, error_message="Failed to process valid response from AI.")
                        hydrated_data = None

                parsing_result = ParsingResult(
                    success=json_data.get("status") == "success" and hydrated_data is not None,
                    data=hydrated_data,
                    parser_path_used=json_data.get("parser_path_used", "ai_service"),
                    template_id_used=json_data.get("template_id_used"),
                    attempt_id=json_data.get("attempt_id"),
                    error_message=json_data.get("error", "Unknown analysis error.")
                )

    except httpx.RequestError as e:
        log.error(f"HTTP request to AI Service failed: {e}")
        parsing_result = ParsingResult(success=False, error_message="Analysis service is unreachable. Please try again later.")
    except Exception as e:
        log.error(f"Critical error during AI service call: {e}", exc_info=True)
        parsing_result = ParsingResult(success=False, error_message=f"An unexpected error occurred: {e}")

    if parsing_result.success and parsing_result.data:
        context.user_data[PARSING_ATTEMPT_ID_KEY] = parsing_result.attempt_id
        context.user_data[ORIGINAL_PARSED_DATA_KEY] = parsing_result.data
        context.user_data[CURRENT_EDIT_DATA_KEY] = parsing_result.data.copy()

        keyboard = build_editable_review_card(parsing_result.data)
        await safe_edit_message(
            context.bot, analyzing_message.chat_id, analyzing_message.message_id,
            text="📊 **Review Parsed Data**\nPlease verify the extracted information:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return AWAIT_REVIEW
    else:
        error_msg = parsing_result.error_message or "Could not recognize a valid trade signal."
        await safe_edit_message(
             context.bot, analyzing_message.chat_id, analyzing_message.message_id,
             text=f"❌ **Analysis Failed**\n{error_msg}",
             parse_mode=ParseMode.HTML,
             reply_markup=None
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

# --- Review State Handlers ---
@uow_transaction
@require_active_user
async def review_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)

    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])

    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    original_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)

    if not current_data or not original_message_id:
        log.warning(f"Parsing review handler called with missing state for user {update.effective_user.id}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Session expired or data lost. Please forward again.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    if action == CallbackAction.CONFIRM.value:
        trade_service: TradeService = get_service(context, "trade_service", TradeService)
        attempt_id = context.user_data.get(PARSING_ATTEMPT_ID_KEY)
        original_data = context.user_data.get(ORIGINAL_PARSED_DATA_KEY)
        raw_text = context.user_data.get(RAW_FORWARDED_TEXT_KEY)
        was_corrected = (original_data != current_data)

        # ✅ HOTFIX: Validate data *before* saving
        try:
            trade_service._validate_recommendation_data(
                current_data['side'], current_data['entry'], 
                current_data['stop_loss'], current_data['targets']
            )
        except ValueError as e:
            # This happens if user confirms empty targets from a failed parse
            log.warning(f"User confirmed invalid data. Error: {e}")
            await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text=f"❌ **Error saving trade:** {e}", reply_markup=None)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

        result = await trade_service.create_trade_from_forwarding_async(
            user_id=str(db_user.telegram_user_id),
            trade_data=current_data,
            original_text=raw_text,
            db_session=db_session
        )

        correction_task = None
        if attempt_id and was_corrected:
            log.debug(f"Recording correction for attempt {attempt_id} via AI Service...")
            
            def serialize_data(data):
                if not data: return {}
                return {
                    "asset": data.get("asset"),
                    "side": data.get("side"),
                    "entry": str(data.get("entry")) if data.get("entry") else None,
                    "stop_loss": str(data.get("stop_loss")) if data.get("stop_loss") else None,
                    "targets": [{"price": str(t["price"]), "close_percent": t.get("close_percent", 0.0)} for t in data.get("targets", [])]
                }
            
            async def record_correction_external(attempt_id, corrected_data, original_data):
                try:
                    base_url = AI_SERVICE_URL.rsplit('/', 1)[0]
                    correction_url = f"{base_url}/record_correction"
                    async with httpx.AsyncClient() as client:
                        await client.post(
                            correction_url,
                            json={
                                "attempt_id": attempt_id,
                                "corrected_data": serialize_data(corrected_data),
                                "original_data": serialize_data(original_data)
                            },
                            timeout=10.0
                        )
                    log.info(f"Successfully recorded correction for attempt {attempt_id} via AI Service.")
                except Exception as e:
                    log.error(f"Failed to record correction via AI Service for attempt {attempt_id}: {e}")
            
            correction_task = asyncio.create_task(
                record_correction_external(attempt_id, current_data, original_data)
            )

        if result.get('success'):
            success_msg = f"✅ **Trade #{result['trade_id']}** for **{result['asset']}** tracked successfully!"
            await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text=success_msg, reply_markup=None)

            if correction_task: await correction_task

            template_used_initially = False
            if was_corrected and attempt_id:
                 try:
                      template_used_initially = context.user_data.get(ORIGINAL_PARSED_DATA_KEY, {}).get("template_id_used") is not None
                 except Exception as e:
                      log.error(f"Error checking template usage for suggestion on attempt {attempt_id}: {e}")

            if was_corrected and not template_used_initially:
                 confirm_kb = build_confirmation_keyboard(
                      CallbackNamespace.SAVE_TEMPLATE, attempt_id,
                      confirm_text="💾 Yes, Save Format", cancel_text="🚫 No, Thanks"
                 )
                 reply_to_msg_id = update.effective_message.reply_to_message.message_id if update.effective_message and update.effective_message.reply_to_message else None
                 await context.bot.send_message(
                      chat_id=query.message.chat_id,
                      text="You corrected the parsed data. Save this message format as a personal template to speed up future analysis?",
                      reply_markup=confirm_kb,
                      reply_to_message_id=reply_to_msg_id
                 )
                 clean_parsing_conversation_state(context)
                 return ConversationHandler.END
            else:
                 log.debug(f"Ending parsing conversation. Corrected={was_corrected}, TemplateUsed={template_used_initially}")
                 clean_parsing_conversation_state(context)
                 return ConversationHandler.END
        else:
            await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text=f"❌ **Error saving trade:** {result.get('error', 'Unknown')}", reply_markup=None)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

    elif action == CallbackAction.EDIT_FIELD.value:
        if not params:
            log.warning("Edit field callback received without field parameter.")
            return AWAIT_REVIEW

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

        cancel_edit_button = InlineKeyboardButton(
            ButtonTexts.CANCEL + " Edit",
            callback_data=CallbackBuilder.create(CallbackNamespace.FORWARD_PARSE, CallbackAction.CANCEL, "edit")
        )
        input_keyboard = InlineKeyboardMarkup([[cancel_edit_button]])

        await safe_edit_message(
            context.bot, query.message.chat_id, original_message_id,
            text=f"📝 **Editing Field: {field_to_edit.replace('_',' ').title()}**\n\n{prompt_text}",
            reply_markup=input_keyboard
        )
        return AWAIT_CORRECTION_VALUE

    elif action == CallbackAction.CANCEL.value:
        await safe_edit_message(context.bot, query.message.chat_id, original_message_id, text="❌ Operation cancelled.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    log.warning(f"Unhandled callback action in review state: {action} from data: {query.data}")
    return AWAIT_REVIEW

# --- Correction Input Handler ---
@uow_transaction
@require_active_user
async def correction_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """Handles the user's text reply with the corrected value for a specific field."""
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)

    field_to_edit = context.user_data.get(EDITING_FIELD_KEY)
    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    original_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)
    user_input = update.message.text.strip() if update.message.text else ""

    try: await update.message.delete()
    except Exception: log.debug("Could not delete user correction message.")

    if not field_to_edit or not current_data or not original_message_id:
        log.warning(f"Correction value handler called with invalid state for user {update.effective_user.id}.")
        await update.effective_chat.send_message("❌ Session error during correction. Please start over.")
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    try:
        validated = False
        temp_data = current_data.copy()

        if field_to_edit == "asset":
            new_asset = user_input.upper()
            if not new_asset: raise ValueError("Asset symbol cannot be empty.")
            temp_data['asset'] = new_asset
            validated = True
        elif field_to_edit == "side":
            side_upper = user_input.upper()
            if side_upper in ["LONG", "SHORT"]: temp_data['side'] = side_upper; validated = True
            else: raise ValueError("Side must be LONG or SHORT.")
        elif field_to_edit in ["entry", "stop_loss"]:
            price = parse_number(user_input)
            if price is None or price <= 0: raise ValueError("Invalid price format (must be > 0).")
            temp_data[field_to_edit] = price
            validated = True
        elif field_to_edit == "targets":
            # ✅ THE FIX (v3.0.2): Use a smarter regex to find numbers and @percentages
            # This finds "0.105", "0.112", "0.12", "0.15", "25%" from the TURTLE example
            # Or "3900", "3950", "4000" from the ETH example
            
            # This regex finds numbers (with decimals/suffixes) optionally followed by @ or %
            pattern = r'([\d.,KMB]+(?:@[\d.,]+%?)?)'
            tokens = re.findall(pattern, user_input, re.IGNORECASE)
            
            # Handle the "0.15 (25% each)" case
            if not tokens and "(25% each)" in user_input:
                # Fallback for complex text: split by common delimiters
                tokens = re.split(r'[\s\n,-]+', user_input)
                # This might produce ['0.105', '', '0.112', '', '0.12', ...]
                # parse_targets_list is designed to handle empty strings
            
            log.debug(f"Smart tokenizer found tokens: {tokens}")
            targets = parse_targets_list(tokens)
            
            if not targets: 
                raise ValueError("Invalid targets format or no valid targets found.")
            
            # Handle " (25% each)" -> apply 25% to all
            if "(25% each)" in user_input and all(t['close_percent'] == 0.0 for t in targets):
                log.debug("Applying '25% each' logic to all targets.")
                for t in targets:
                    t['close_percent'] = 25.0
            
            temp_data['targets'] = targets
            validated = True

        if validated:
            trade_service = get_service(context, "trade_service", TradeService)
            temp_data.setdefault('asset', current_data.get('asset'))
            temp_data.setdefault('side', current_data.get('side'))
            temp_data.setdefault('entry', current_data.get('entry'))
            temp_data.setdefault('stop_loss', current_data.get('stop_loss'))
            temp_data.setdefault('targets', current_data.get('targets'))

            trade_service._validate_recommendation_data(
                 temp_data['side'], temp_data['entry'], temp_data['stop_loss'], temp_data['targets']
            )

            current_data[field_to_edit] = temp_data[field_to_edit]
            log.info(f"Field '{field_to_edit}' corrected successfully by user {update.effective_user.id}")
            context.user_data.pop(EDITING_FIELD_KEY, None)

            keyboard = build_editable_review_card(current_data)
            await safe_edit_message(
                 context.bot, update.effective_chat.id, original_message_id,
                 text="✅ Value updated. Please review again:",
                 reply_markup=keyboard,
                 parse_mode=ParseMode.HTML
            )
            return AWAIT_REVIEW
        else:
             raise ValueError(f"Internal validation failed for field '{field_to_edit}'.")

    except ValueError as e:
        log.warning(f"Invalid correction input by user {update.effective_user.id} for field '{field_to_edit}': {e}")
        cancel_edit_button = InlineKeyboardButton(
            ButtonTexts.CANCEL + " Edit",
            callback_data=CallbackBuilder.create(CallbackNamespace.FORWARD_PARSE, CallbackAction.CANCEL, "edit")
        )
        await safe_edit_message(
            context.bot, update.effective_chat.id, original_message_id,
            text=f"⚠️ **Invalid Input:** {e}\nPlease try again for **{field_to_edit.replace('_',' ').title()}** or cancel:",
            reply_markup=InlineKeyboardMarkup([[cancel_edit_button]]),
            parse_mode=ParseMode.HTML
        )
        return AWAIT_CORRECTION_VALUE

    except Exception as e:
        log.error(f"Error handling correction for {field_to_edit} by user {update.effective_user.id}: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ An unexpected error occurred during correction. Operation cancelled."
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END


# --- Save Template Confirmation Handler (Standalone - Outside Conversation) ---
@uow_transaction
@require_active_user
async def save_template_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()

    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    attempt_id = int(params[0]) if params and params[0].isdigit() else None

    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=query.message.text_html, reply_markup=None)

    if action == CallbackAction.CONFIRM.value and attempt_id:
        log.debug(f"User confirmed saving template for attempt {attempt_id}. Calling AI Service.")
        try:
            base_url = AI_SERVICE_URL.rsplit('/', 1)[0]
            suggest_url = f"{base_url}/suggest_template"
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    suggest_url,
                    json={"attempt_id": attempt_id, "user_id": db_user.id},
                    timeout=10.0
                )
                response.raise_for_status()
                
                res_json = response.json()
                if res_json.get("success"):
                    await query.message.reply_text(f"✅ Template suggestion (ID: {res_json.get('template_id')}) submitted for review.")
                else:
                    await query.message.reply_text(f"ℹ️ Template suggestion failed: {res_json.get('error', 'Unknown')}")
        
        except httpx.RequestError as e:
            log.error(f"Error calling AI Service to suggest template for attempt {attempt_id}: {e}")
            await query.message.reply_text("❌ Error submitting template suggestion: Service unreachable.")
        except Exception as e:
            log.error(f"Error saving template suggestion from attempt {attempt_id}: {e}", exc_info=True)
            await query.message.reply_text("❌ Error submitting template suggestion.")
    else:
        await query.message.reply_text("ℹ️ Template suggestion discarded.")

    return ConversationHandler.END


# --- General Cancel / Fallback ---
async def cancel_parsing_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message_text = "❌ Operation cancelled."
    target_chat_id = update.effective_chat.id
    target_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)

    if update.callback_query:
        await update.callback_query.answer()
        if not target_message_id and update.callback_query.message:
            target_message_id = update.callback_query.message.message_id

    clean_parsing_conversation_state(context)

    if target_message_id:
        await safe_edit_message(context.bot, target_chat_id, target_message_id, text=message_text, reply_markup=None)
    elif update.message:
        await update.message.reply_text(message_text)
    else:
        await context.bot.send_message(chat_id=target_chat_id, text=message_text)

    return ConversationHandler.END


# --- Registration ---
def register_forward_parsing_handlers(app: Application):
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(
            filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            forwarded_message_handler
        )],
        states={
            AWAIT_REVIEW: [CallbackQueryHandler(
                review_callback_handler,
                pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:(?:{CallbackAction.CONFIRM.value}|{CallbackAction.EDIT_FIELD.value}|{CallbackAction.CANCEL.value}):"
            )],
            AWAIT_CORRECTION_VALUE: [MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                correction_value_handler
            )],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_parsing_conversation),
            CallbackQueryHandler(cancel_parsing_conversation, pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:{CallbackAction.CANCEL.value}:edit"),
            CallbackQueryHandler(cancel_parsing_conversation, pattern="^.*"),
            MessageHandler(filters.ALL & filters.ChatType.PRIVATE, cancel_parsing_conversation)
        ],
        name="forward_parsing_conversation",
        per_user=True, per_chat=True,
        persistent=False,
        conversation_timeout=MANAGEMENT_TIMEOUT,
        per_message=False
    )
    app.add_handler(conv_handler, group=1)

    app.add_handler(CallbackQueryHandler(
        save_template_confirm_handler,
        pattern=f"^{CallbackNamespace.SAVE_TEMPLATE.value}:"
    ), group=1)

# --- END of forward parsing handler ---