# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---
"""
Handles the user flow for parsing a forwarded text message (v3.2.4 - Callback Hotfix).
THE FIX (R1-S1): Major strategic update.
    - Implements the "Trader-First" Golden Rule (Watchlist vs. Activated).
    - `forwarded_message_handler` now captures origin date and chat info using modern PTB v21+ API.
    - `build_editable_review_card` now shows "Activate Trade" and "Watch Channel" buttons.
    - `review_callback_handler` now calls trade_service with the selected status (`PENDING_ACTIVATION` or `WATCHLIST`)
      and passes the new audit data (original_published_at, channel_info).
HOTFIX v3.2.2: Fixed SyntaxError: unterminated string literal.
HOTFIX v3.2.3: Removed invalid character from comment to fix SyntaxError (U+2705).
✅ HOTFIX v3.2.4 (This file): Corrected the `pattern` in ConversationHandler
    to use `CallbackAction.WATCH_CHANNEL.value` instead of the literal string "WATCH_CHANNEL",
    fixing the bug where the button fell through to the 'cancel' fallback.
"""

import logging
import asyncio
import httpx
import os
import re
import html
from decimal import Decimal
from typing import Dict, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
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
    CallbackBuilder, CallbackNamespace, CallbackAction,
    build_editable_review_card, ButtonTexts
)
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list
from capitalguard.interfaces.telegram.management_handlers import (
    handle_management_timeout, update_management_activity, MANAGEMENT_TIMEOUT
)
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

# New key for auditing
FORWARD_AUDIT_DATA_KEY = "forward_audit_data"

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL")

if not AI_SERVICE_URL:
    log.critical(
        "AI_SERVICE_URL environment variable is not set! Forward parsing will fail."
    )


def clean_parsing_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    """Cleans up all keys related to the parsing conversation."""
    keys_to_pop = [
        PARSING_ATTEMPT_ID_KEY, ORIGINAL_PARSED_DATA_KEY, CURRENT_EDIT_DATA_KEY,
        EDITING_FIELD_KEY, RAW_FORWARDED_TEXT_KEY, ORIGINAL_MESSAGE_ID_KEY,
        LAST_ACTIVITY_KEY, 'fwd_msg_text', 'pending_trade',
        FORWARD_AUDIT_DATA_KEY,
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    # context._user_id may not exist in some contexts; guard access
    user_id = getattr(context, "_user_id", None)
    log.debug(f"Parsing conversation state cleared for user {user_id}.")


async def smart_safe_edit(
    bot: Bot, chat_id: int, message_id: int,
    text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML
) -> bool:
    """
    Tries to edit a message, falling back to parse_mode=None if HTML/Markdown fails.
    This prevents the "unsupported start tag" freeze.
    """
    try:
        # First attempt (usually HTML or Markdown)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
        return True
    except BadRequest as e:
        # Common benign case: not modified
        if "message is not modified" in str(e).lower():
            return True

        # Check for the specific entity parsing error
        if "can't parse entities" in str(e).lower() or "unsupported start tag" in str(e).lower():
            log.warning(
                f"HTML/Markdown parse failed for msg {chat_id}:{message_id}. Retrying with parse_mode=None. Error: {e}"
            )
            try:
                # Fallback: Strip tags and send as plain text
                if text is None:
                    clean_text = ""
                else:
                    clean_text = re.sub(r'<[^>]+>', '', text)
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=clean_text,
                    reply_markup=reply_markup,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
                return True
            except Exception as e_retry:
                loge.error(
                    f"Failed to edit message {chat_id}:{message_id} even after retry: {e_retry}",
                    exc_info=True
                )
                return False

        # Other BadRequests
        loge.warning(f"Handled BadRequest in smart_safe_edit: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError in smart_safe_edit {chat_id}:{message_id}: {e}")
        return False
    except Exception as e_other:
        loge.exception(f"Unexpected error in smart_safe_edit {chat_id}:{message_id}: {e_other}")
        return False


@uow_transaction
@require_active_user
async def forwarded_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    message = update.message
    if not message or not message.text or len(message.text) < 10:
        return ConversationHandler.END

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

    # Capture audit data (Timestamp and Channel)
    original_published_at = None
    channel_info = None

    # PTB v21+ uses message.forward_origin
    if getattr(message, "forward_origin", None):
        forward_origin = message.forward_origin
        original_published_at = getattr(forward_origin, "date", None)
        # Check if origin has chat attribute
        origin_chat = getattr(forward_origin, "chat", None)
        if origin_chat:
            channel_info = {
                "id": getattr(origin_chat, "id", None),
                "title": getattr(origin_chat, "title", "Unknown Channel")
            }

    if not original_published_at:
        await message.reply_text(
            "❌ **Error:** This message seems to be a copy-paste, not a forward.\n"
            "To analyze, please **forward** the original message directly from the channel."
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    context.user_data[RAW_FORWARDED_TEXT_KEY] = message.text
    context.user_data[FORWARD_AUDIT_DATA_KEY] = {
        "original_published_at": original_published_at.isoformat() if original_published_at else None,
        "channel_info": channel_info
    }

    analyzing_message = await message.reply_text("⏳ Analyzing forwarded message...")
    context.user_data[ORIGINAL_MESSAGE_ID_KEY] = analyzing_message.message_id
    user_db_id = db_user.id
    parsing_result: ParsingResult
    hydrated_data = None

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
                try:
                    error_detail = response.json().get("detail", "Analysis service failed.")
                except Exception:
                    error_detail = "Analysis service failed."
                parsing_result = ParsingResult(success=False, error_message=f"Error {response.status_code}: {error_detail}")
            else:
                json_data = response.json()
                if json_data.get("status") == "success" and json_data.get("data"):
                    try:
                        raw = json_data["data"]
                        hydrated_data = {
                            "asset": raw.get("asset"),
                            "side": raw.get("side"),
                            "entry": parse_number(raw.get("entry")),
                            "stop_loss": parse_number(raw.get("stop_loss")),
                            "targets": parse_targets_list(
                                [f"{t.get('price')}@{t.get('close_percent')}" for t in raw.get("targets", [])]
                            )
                        }

                        trade_service: TradeService = get_service(context, "trade_service", TradeService)
                        trade_service._validate_recommendation_data(
                            hydrated_data['side'], hydrated_data['entry'],
                            hydrated_data['stop_loss'], hydrated_data['targets']
                        )

                        parsing_result = ParsingResult(
                            success=True,
                            data=hydrated_data,
                            parser_path_used=json_data.get("parser_path_used", "ai_service"),
                            template_id_used=json_data.get("template_id_used"),
                            attempt_id=json_data.get("attempt_id")
                        )

                    except (ValueError, TypeError) as e:
                        log.warning(
                            f"AI Service returned invalid data for attempt {json_data.get('attempt_id')}: {e}"
                        )
                        parsing_result = ParsingResult(success=False, error_message=f"Analysis Failed: {e}")
                        hydrated_data = None
                    except Exception as e:
                        log.error(f"Failed to re-hydrate JSON from AI service: {e}")
                        parsing_result = ParsingResult(success=False, error_message="Failed to process valid response from AI.")
                        hydrated_data = None
                else:
                    parsing_result = ParsingResult(
                        success=False,
                        attempt_id=json_data.get("attempt_id"),
                        error_message=json_data.get("error", "Unknown analysis error.")
                    )
    except httpx.RequestError as e:
        log.error(f"HTTP request to AI Service failed: {e}")
        parsing_result = ParsingResult(success=False, error_message="Analysis service is unreachable. Please try again later.")
    except Exception as e:
        log.error(f"Critical error during AI service call: {e}", exc_info=True)
        parsing_result = ParsingResult(success=False, error_message=f"An unexpected error occurred: {e}")

    # Proceed with the result
    if parsing_result.success and hydrated_data:
        # Save attempt_id for state recovery
        context.user_data[PARSING_ATTEMPT_ID_KEY] = parsing_result.attempt_id
        context.user_data[ORIGINAL_PARSED_DATA_KEY] = hydrated_data
        context.user_data[CURRENT_EDIT_DATA_KEY] = hydrated_data.copy()

        # Pass audit data (channel name) to the review card builder
        channel_name = channel_info.get("title") if channel_info else "Unknown Channel"
        keyboard = build_editable_review_card(hydrated_data, channel_name=channel_name)

        # Use smart edit
        await smart_safe_edit(
            context.bot, analyzing_message.chat.id, analyzing_message.message_id,
            text=f"📊 **Review Parsed Data**\n*Source:* `{channel_name}`\n\nPlease verify the data and choose an action:",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return AWAIT_REVIEW
    else:
        # Escape the error message
        escaped = html.escape(parsing_result.error_message or "Could not recognize a valid trade signal.")
        await smart_safe_edit(
            context.bot, analyzing_message.chat.id, analyzing_message.message_id,
            text=f"❌ **Analysis Failed**\n{escaped}",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END


@uow_transaction
@require_active_user
async def review_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
    update_management_activity(context)

    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])

    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    original_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)

    # Get audit data
    audit_data = context.user_data.get(FORWARD_AUDIT_DATA_KEY)

    # State Recovery Logic
    if not current_data or not original_message_id or audit_data is None:
        log.warning(f"State lost for user {update.effective_user.id}. Attempting DB recovery...")
        attempt_id = context.user_data.get(PARSING_ATTEMPT_ID_KEY)

        if not original_message_id and query.message:
            original_message_id = query.message.message_id
            context.user_data[ORIGINAL_MESSAGE_ID_KEY] = original_message_id

        if attempt_id and original_message_id:
            try:
                attempt = db_session.get(ParsingAttempt, attempt_id)
                if attempt and getattr(attempt, "result_data", None):
                    raw = attempt.result_data
                    restored = {
                        "asset": raw.get("asset"),
                        "side": raw.get("side"),
                        "entry": parse_number(raw.get("entry")),
                        "stop_loss": parse_number(raw.get("stop_loss")),
                        "targets": parse_targets_list(
                            [f"{t.get('price')}@{t.get('close_percent')}" for t in raw.get("targets", [])]
                        )
                    }
                    context.user_data[CURRENT_EDIT_DATA_KEY] = restored
                    context.user_data[ORIGINAL_PARSED_DATA_KEY] = restored
                    current_data = restored

                    # Attempt to recover audit data (brittle)
                    if not audit_data:
                        # If we have raw_content, store as RAW_FORWARDED_TEXT_KEY
                        if getattr(attempt, "raw_content", None):
                            context.user_data[RAW_FORWARDED_TEXT_KEY] = attempt.raw_content
                            # We cannot reliably recover original_published_at or channel_info
                            log.warning(f"Session recovered for {attempt_id}, but audit data (timestamp/channel) is lost.")
                    channel_name = "Unknown (Recovered)"
                    if audit_data:
                        channel_name = audit_data.get("channel_info", {}).get("title", "Unknown")
                    keyboard = build_editable_review_card(restored, channel_name=channel_name)
                    await smart_safe_edit(
                        context.bot, query.message.chat.id, original_message_id,
                        text=f"🔄 Session recovered. Please review again:\n*Source:* `{channel_name}`",
                        reply_markup=keyboard,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    log.info(f"Successfully recovered session for user {update.effective_user.id} from attempt {attempt_id}")
                else:
                    raise ValueError("Attempt not found or has no result data.")
            except Exception as e_recover:
                log.error(f"Failed to recover session from DB for attempt {attempt_id}: {e_recover}")
                try:
                    await smart_safe_edit(context.bot, query.message.chat.id, query.message.message_id,
                                          text="❌ Session expired or data lost. Please forward again.", reply_markup=None)
                except Exception:
                    # If smart edit fails, fallback to simple reply
                    await query.message.reply_text("❌ Session expired or data lost. Please forward again.")
                clean_parsing_conversation_state(context)
                return ConversationHandler.END
        else:
            # If recovery fails (no attempt_id), then fail
            try:
                await smart_safe_edit(context.bot, query.message.chat.id, query.message.message_id,
                                      text="❌ Session expired or data lost. Please forward again.", reply_markup=None)
            except Exception:
                await query.message.reply_text("❌ Session expired or data lost. Please forward again.")
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

    # Handle actions: ACTIVATE_TRADE (CONFIRM) and WATCH_CHANNEL
    if action in (CallbackAction.CONFIRM.value, CallbackAction.WATCH_CHANNEL.value):
        trade_service: TradeService = get_service(context, "trade_service", TradeService)
        attempt_id = context.user_data.get(PARSING_ATTEMPT_ID_KEY)
        original_data = context.user_data.get(ORIGINAL_PARSED_DATA_KEY)
        raw_text = context.user_data.get(RAW_FORWARDED_TEXT_KEY)
        was_corrected = (original_data != current_data)

        # Determine status based on button pressed
        status_to_set = "PENDING_ACTIVATION" if action == CallbackAction.CONFIRM.value else "WATCHLIST"
        action_verb = "Activated" if status_to_set == "PENDING_ACTIVATION" else "Added to Watchlist"

        try:
            trade_service._validate_recommendation_data(
                current_data['side'], current_data['entry'],
                current_data['stop_loss'], current_data['targets']
            )
        except ValueError as e:
            log.warning("ValidationFailureOnConfirm", extra={
                "user": db_user.telegram_user_id,
                "attempt_id": attempt_id,
                "error": str(e),
                "context": "forward_parsing.review_callback_handler"
            })
            error_text = f"❌ **Error saving trade:** {html.escape(str(e))}"
            await smart_safe_edit(context.bot, query.message.chat.id, original_message_id,
                                  text=error_text, reply_markup=None, parse_mode=ParseMode.HTML)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

        # Call trade_service method and pass audit data
        try:
            result = await trade_service.create_trade_from_forwarding_async(
                user_id=str(db_user.telegram_user_id),
                trade_data=current_data,
                original_text=raw_text,
                db_session=db_session,
                status_to_set=status_to_set,
                original_published_at=audit_data.get("original_published_at") if audit_data else None,
                channel_info=audit_data.get("channel_info") if audit_data else None
            )
        except Exception as e:
            log.error(f"Error calling create_trade_from_forwarding_async: {e}", exc_info=True)
            await smart_safe_edit(context.bot, query.message.chat.id, original_message_id,
                                  text="❌ Error saving trade. Please try again later.", reply_markup=None)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

        correction_task = None
        if attempt_id and was_corrected:
            log.debug(f"Recording correction for attempt {attempt_id} via AI Service...")

            def serialize_data(data):
                if not data:
                    return {}
                return {
                    "asset": data.get("asset"),
                    "side": data.get("side"),
                    "entry": str(data.get("entry")) if data.get("entry") is not None else None,
                    "stop_loss": str(data.get("stop_loss")) if data.get("stop_loss") is not None else None,
                    "targets": [{"price": str(t["price"]), "close_percent": t.get("close_percent", 0.0)} for t in data.get("targets", [])]
                }

            async def record_correction_external(attempt_id_inner, corrected_data, original_data_inner):
                try:
                    base_url = AI_SERVICE_URL.rsplit('/', 1)[0]
                    correction_url = f"{base_url}/record_correction"
                    async with httpx.AsyncClient() as client:
                        await client.post(
                            correction_url,
                            json={
                                "attempt_id": attempt_id_inner,
                                "corrected_data": serialize_data(corrected_data),
                                "original_data": serialize_data(original_data_inner)
                            },
                            timeout=10.0
                        )
                    log.info(f"Successfully recorded correction for attempt {attempt_id_inner} via AI Service.")
                except Exception as e:
                    log.error(f"Failed to record correction via AI Service for attempt {attempt_id_inner}: {e}")

            correction_task = asyncio.create_task(
                record_correction_external(attempt_id, current_data, original_data)
            )

        if result.get('success'):
            success_msg = f"✅ **Trade #{result['trade_id']}** for **{result['asset']}** {action_verb} successfully!"
            await smart_safe_edit(context.bot, query.message.chat.id, original_message_id, text=success_msg, reply_markup=None)

            if correction_task:
                try:
                    await correction_task
                except Exception:
                    # best-effort; don't fail the flow
                    pass

            # Template suggestion logic
            template_used_initially = False
            try:
                template_used_initially = bool(context.user_data.get(ORIGINAL_PARSED_DATA_KEY, {}).get("template_id_used"))
            except Exception as e:
                log.error(f"Error checking template usage for suggestion on attempt {attempt_id}: {e}")

            if was_corrected and not template_used_initially:
                # Re-import build_confirmation_keyboard as needed
                try:
                    from capitalguard.interfaces.telegram.keyboards import build_confirmation_keyboard
                    confirm_kb = build_confirmation_keyboard(
                        CallbackNamespace.SAVE_TEMPLATE, attempt_id,
                        confirm_text="💾 Yes, Save Format", cancel_text="🚫 No, Thanks"
                    )
                except Exception:
                    confirm_kb = None

                reply_to_msg_id = update.effective_message.reply_to_message.message_id if update.effective_message and update.effective_message.reply_to_message else None
                try:
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text="You corrected the parsed data.\nSave this message format as a personal template to speed up future analysis?",
                        reply_markup=confirm_kb,
                        reply_to_message_id=reply_to_msg_id
                    )
                except Exception:
                    # If sending fails, log and continue
                    log.exception("Failed to send template suggestion prompt")

                clean_parsing_conversation_state(context)
                return ConversationHandler.END
            else:
                log.debug(f"Ending parsing conversation. Corrected={was_corrected}, TemplateUsed={template_used_initially}")
                clean_parsing_conversation_state(context)
                return ConversationHandler.END
        else:
            error_text = f"❌ **Error saving trade:** {html.escape(result.get('error', 'Unknown'))}"
            await smart_safe_edit(context.bot, query.message.chat.id, original_message_id, text=error_text, reply_markup=None, parse_mode=ParseMode.HTML)
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

        await smart_safe_edit(
            context.bot, query.message.chat.id, original_message_id,
            text=f"📝 **Editing Field: {field_to_edit.replace('_',' ').title()}**\n\n{prompt_text}",
            reply_markup=input_keyboard
        )
        return AWAIT_CORRECTION_VALUE

    elif action == CallbackAction.CANCEL.value:
        await smart_safe_edit(context.bot, query.message.chat.id, original_message_id, text="❌ Operation cancelled.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    log.warning(f"Unhandled callback action in review state: {action} from data: {query.data}")
    return AWAIT_REVIEW


@uow_transaction
@require_active_user
async def correction_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
    update_management_activity(context)

    field_to_edit = context.user_data.get(EDITING_FIELD_KEY)
    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    original_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)
    audit_data = context.user_data.get(FORWARD_AUDIT_DATA_KEY)
    user_input = update.message.text.strip() if update.message and update.message.text else ""

    try:
        if update.message:
            await update.message.delete()
    except Exception:
        log.debug("Could not delete user correction message.")

    if not field_to_edit or not current_data or not original_message_id or audit_data is None:
        log.warning(f"Correction value handler called with invalid state for user {update.effective_user.id}.")
        try:
            await update.effective_chat.send_message("❌ Session error during correction. Please start over.")
        except Exception:
            log.debug("Failed to send session error message.")
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    try:
        validated = False
        temp_data = current_data.copy()

        if field_to_edit == "asset":
            new_asset = user_input.upper()
            if not new_asset:
                raise ValueError("Asset symbol cannot be empty.")
            temp_data['asset'] = new_asset
            validated = True

        elif field_to_edit == "side":
            side_upper = user_input.upper()
            if side_upper in ["LONG", "SHORT"]:
                temp_data['side'] = side_upper
                validated = True
            else:
                raise ValueError("Side must be LONG or SHORT.")

        elif field_to_edit in ["entry", "stop_loss"]:
            price = parse_number(user_input)
            if price is None or price <= 0:
                raise ValueError("Invalid price format (must be > 0).")
            temp_data[field_to_edit] = price
            validated = True

        elif field_to_edit == "targets":
            pattern = r'([\d.,KMB]+(?:@[\d.,]+%?)?)'
            tokens = re.findall(pattern, user_input, re.IGNORECASE)

            if not tokens and "(25% each)" in user_input.lower():
                just_numbers = re.findall(r'([\d.,KMB]+)', user_input)
                tokens = [f"{num}@25" for num in just_numbers]
            elif not tokens:
                tokens = re.split(r'[\s\n,]+', user_input)

            log.debug(f"Smart tokenizer found tokens: {tokens}")
            targets = parse_targets_list(tokens)

            if not targets:
                raise ValueError("Invalid targets format or no valid targets found.")

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

            # Pass channel name to review card
            channel_name = audit_data.get("channel_info", {}).get("title", "Unknown Channel") if audit_data else "Unknown Channel"
            keyboard = build_editable_review_card(current_data, channel_name=channel_name)

            await smart_safe_edit(
                context.bot, update.effective_chat.id, original_message_id,
                text=f"✅ Value updated.\nPlease review again:\n*Source:* `{channel_name}`",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return AWAIT_REVIEW
        else:
            raise ValueError(f"Internal validation failed for field '{field_to_edit}'.")

    except ValueError as e:
        log.warning("ValidationFailureOnCorrection", extra={
            "user": db_user.telegram_user_id,
            "attempt_id": context.user_data.get(PARSING_ATTEMPT_ID_KEY),
            "field": field_to_edit,
            "input": user_input,
            "error": str(e),
            "context": "forward_parsing.correction_value_handler"
        })
        cancel_edit_button = InlineKeyboardButton(
            ButtonTexts.CANCEL + " Edit",
            callback_data=CallbackBuilder.create(CallbackNamespace.FORWARD_PARSE, CallbackAction.CANCEL, "edit")
        )
        error_text = f"⚠️ **Invalid Input:** {html.escape(str(e))}\nPlease try again for **{field_to_edit.replace('_',' ').title()}** or cancel:"
        await smart_safe_edit(
            context.bot, update.effective_chat.id, original_message_id,
            text=error_text,
            reply_markup=InlineKeyboardMarkup([[cancel_edit_button]]),
            parse_mode=ParseMode.HTML
        )
        return AWAIT_CORRECTION_VALUE

    except Exception as e:
        log.error(f"Error handling correction for {field_to_edit} by user {update.effective_user.id}: {e}", exc_info=True)
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An unexpected error occurred during correction. Operation cancelled."
            )
        except Exception:
            log.debug("Failed to send unexpected error message.")
        clean_parsing_conversation_state(context)
        return ConversationHandler.END


@uow_transaction
@require_active_user
async def save_template_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()

    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    attempt_id = int(params[0]) if params and isinstance(params[0], str) and params[0].isdigit() else None

    # Try to revert message to HTML text (if possible)
    try:
        await smart_safe_edit(context.bot, query.message.chat.id, query.message.message_id, text=query.message.text_html, reply_markup=None)
    except Exception:
        # ignore; best-effort
        pass

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


async def cancel_parsing_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message_text = "❌ Operation cancelled."
    target_chat_id = update.effective_chat.id if update.effective_chat else None
    target_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)

    if update.callback_query:
        await update.callback_query.answer()
        if not target_message_id and update.callback_query.message:
            target_message_id = update.callback_query.message.message_id

    clean_parsing_conversation_state(context)

    try:
        if target_message_id and target_chat_id:
            await smart_safe_edit(context.bot, target_chat_id, target_message_id, text=message_text, reply_markup=None)
        elif update.message:
            await update.message.reply_text(message_text)
        elif target_chat_id:
            await context.bot.send_message(chat_id=target_chat_id, text=message_text)
    except Exception:
        log.debug("Failed to send cancel confirmation; ignoring.")

    return ConversationHandler.END


def register_forward_parsing_handlers(app: Application):
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(
            filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            forwarded_message_handler
        )],
        states={
            AWAIT_REVIEW: [CallbackQueryHandler(
                review_callback_handler,
                # ✅ THE FIX: Changed literal "WATCH_CHANNEL" to the enum value
                pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:(?:{CallbackAction.CONFIRM.value}|{CallbackAction.WATCH_CHANNEL.value}|{CallbackAction.EDIT_FIELD.value}|{CallbackAction.CANCEL.value}):"
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
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---