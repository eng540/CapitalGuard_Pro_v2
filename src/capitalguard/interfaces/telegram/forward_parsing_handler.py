#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---
# File: src/capitalguard/interfaces/telegram/forward_parsing_handler.py
# Version: 5.0.0 (Stateful Owner)
# ✅ THE FIX: (Protocol 1) دمج منطق قاعدة البيانات، وفصل `ai-service`.
#    - كلا معالجي النص والصور يقومان الآن بـ:
#    - 1. إنشاء `ParsingAttempt(status='pending')` في قاعدة البيانات *قبل* استدعاء الخدمة.
#    - 2. استدعاء `ai-service` (عبر `httpx` أو `ImageParsingService`).
#    - 3. تحديث `ParsingAttempt` بالنتيجة (نجاح/فشل، بيانات، زمن انتقال).
#    - 4. تنفيذ `_record_correction_local` (الجديدة) لمعالجة التصحيحات محليًا.
#    - 5. تنفيذ `save_template_confirm_handler` (المعدلة) لمعالجة اقتراحات القوالب محليًا.
# 🎯 IMPACT: خدمة `api` (تحديدًا هذا المعالج) أصبحت تمتلك دورة حياة التحليل وحالتها.

import logging
import asyncio
import httpx
import os
import re
import html
import json # ✅ ADDED
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
# ❌ REMOVED: ParsingResult (no longer needed)
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.image_parsing_service import ImageParsingService 
from capitalguard.interfaces.telegram.keyboards import (
    CallbackBuilder, CallbackNamespace, CallbackAction,
    build_editable_review_card, ButtonTexts
)
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list
from capitalguard.interfaces.telegram.management_handlers import (
    handle_management_timeout, update_management_activity, MANAGEMENT_TIMEOUT
)
# ✅ ADDED: Import DB models for the new logic
from capitalguard.infrastructure.db.models import ParsingAttempt, ParsingTemplate, User
from capitalguard.infrastructure.db.repository import ParsingRepository # Import repo class

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- Conversation States (Shared) ---
(AWAIT_REVIEW, AWAIT_CORRECTION_VALUE, AWAIT_SAVE_TEMPLATE_CONFIRM) = range(3)

# --- State Keys ---
PARSING_ATTEMPT_ID_KEY = "parsing_attempt_id"
ORIGINAL_PARSED_DATA_KEY = "original_parsed_data"
CURRENT_EDIT_DATA_KEY = "current_edit_data"
EDITING_FIELD_KEY = "editing_field_key"
RAW_FORWARDED_TEXT_KEY = "raw_forwarded_text"
ORIGINAL_MESSAGE_ID_KEY = "parsing_review_message_id"
LAST_ACTIVITY_KEY = "last_activity_management"
FORWARD_AUDIT_DATA_KEY = "forward_audit_data"

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL")

if not AI_SERVICE_URL:
    log.critical(
        "AI_SERVICE_URL environment variable is not set! Forward parsing will fail."
    )

# --- ✅ ADDED: Local data conversion helpers ---
def _serialize_data_for_db(data: Dict[str, Any]) -> Dict[str, Any]:
    """Converts Decimals to strings for JSONB storage."""
    if not data:
        return {}
    
    entry = data.get("entry")
    stop_loss = data.get("stop_loss")
    targets = data.get("targets", [])

    return {
        "asset": data.get("asset"),
        "side": data.get("side"),
        "entry": str(entry) if entry is not None else None,
        "stop_loss": str(stop_loss) if stop_loss is not None else None,
        "targets": [
            {
                "price": str(t.get("price")) if t.get("price") is not None else "0",
                "close_percent": t.get("close_percent", 0.0)
            } for t in targets
        ],
        "market": data.get("market", "Futures"),
        "order_type": data.get("order_type", "LIMIT"),
        "notes": data.get("notes")
    }

# --- (clean_parsing_conversation_state, smart_safe_edit remain the same) ---
def clean_parsing_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    keys_to_pop = [
        PARSING_ATTEMPT_ID_KEY, ORIGINAL_PARSED_DATA_KEY, CURRENT_EDIT_DATA_KEY,
        EDITING_FIELD_KEY, RAW_FORWARDED_TEXT_KEY, ORIGINAL_MESSAGE_ID_KEY,
        LAST_ACTIVITY_KEY, 'fwd_msg_text', 'pending_trade',
        FORWARD_AUDIT_DATA_KEY,
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)
    user_id = getattr(context, "_user_id", None)
    log.debug(f"Parsing conversation state cleared for user {user_id}.")

async def smart_safe_edit(
    bot: Bot, chat_id: int, message_id: int,
    text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML
) -> bool:
    try:
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
        if "message is not modified" in str(e).lower():
            return True
        if "can't parse entities" in str(e).lower() or "unsupported start tag" in str(e).lower():
            log.warning(
                f"HTML/Markdown parse failed for msg {chat_id}:{message_id}. Retrying with parse_mode=None. Error: {e}"
            )
            try:
                clean_text = re.sub(r'<[^>]+>', '', text or "")
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
        loge.warning(f"Handled BadRequest in smart_safe_edit: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError in smart_safe_edit {chat_id}:{message_id}: {e}")
        return False
    except Exception as e_other:
        loge.exception(f"Unexpected error in smart_safe_edit {chat_id}:{message_id}: {e_other}")
        return False
# --- End Helpers ---


# --- Entry Point 1: Text Forward (REFACTORED) ---
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
        await message.reply_text("❌ Feature unavailable: The analysis service is not configured.")
        return ConversationHandler.END

    # --- (Audit data capture remains the same) ---
    original_published_at = None
    channel_info = None
    if getattr(message, "forward_origin", None):
        forward_origin = message.forward_origin
        original_published_at = getattr(forward_origin, "date", None)
        origin_chat = getattr(forward_origin, "chat", None)
        if origin_chat:
            channel_info = {
                "id": getattr(origin_chat, "id", None),
                "title": getattr(origin_chat, "title", "Unknown Channel")
            }
    if not original_published_at:
        await message.reply_text("❌ **Error:** Please **forward** the original message, not a copy-paste.")
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    context.user_data[RAW_FORWARDED_TEXT_KEY] = message.text
    context.user_data[FORWARD_AUDIT_DATA_KEY] = {
        "original_published_at": original_published_at.isoformat() if original_published_at else None,
        "channel_info": channel_info
    }
    # --- End Audit ---

    analyzing_message = await message.reply_text("⏳ Analyzing forwarded message...")
    context.user_data[ORIGINAL_MESSAGE_ID_KEY] = analyzing_message.message_id
    user_db_id = db_user.id
    
    # ✅ --- (Protocol 1) NEW DB LOGIC ---
    parsing_repo = ParsingRepository(db_session)
    attempt = parsing_repo.add_attempt(
        user_id=user_db_id,
        raw_content=message.text,
        was_successful=False,
        parser_path_used="pending"
    )
    attempt_id = attempt.id
    context.user_data[PARSING_ATTEMPT_ID_KEY] = attempt_id
    # We commit here to get the ID, even if the API call fails later
    db_session.commit() 
    # --- End DB Logic ---

    hydrated_data = None
    parsing_result_json = None
    final_error_message = "Could not recognize a valid trade signal."
    parser_path_used = "failed"
    latency_ms = 0
    start_time = time.monotonic()

    try:
        log.debug(f"Calling AI Service at {AI_SERVICE_URL} for user {user_db_id} (Attempt {attempt_id})")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{AI_SERVICE_URL}/ai/parse", # Use full path
                json={"text": message.text, "user_id": user_db_id},
                timeout=20.0
            )
        
        # Get latency from service if available, else calculate
        json_data = response.json()
        latency_ms = json_data.get("latency_ms", int((time.monotonic() - start_time) * 1000))
        
        if response.status_code >= 400:
            log.error(f"AI Service returned HTTP {response.status_code}: {response.text[:200]}")
            final_error_message = f"Error {response.status_code}: Analysis service failed."
        else:
            parsing_result_json = json_data # Store raw response
            parser_path_used = json_data.get("parser_path_used", "ai_service")

            if json_data.get("status") == "success" and json_data.get("data"):
                try:
                    # ✅ Data now comes as strings, must parse to Decimals
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
                except (ValueError, TypeError) as e:
                    log.warning(f"AI Service returned invalid data for attempt {attempt_id}: {e}")
                    final_error_message = f"Analysis Failed: {e}"
                    hydrated_data = None
                except Exception as e:
                    log.error(f"Failed to re-hydrate JSON from AI service: {e}")
                    final_error_message = "Failed to process valid response from AI."
                    hydrated_data = None
            else:
                final_error_message = json_data.get("error", "Unknown analysis error.")

    except httpx.RequestError as e:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        log.error(f"HTTP request to AI Service failed: {e}")
        final_error_message = "Analysis service is unreachable. Please try again later."
    except Exception as e:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        log.error(f"Critical error during AI service call: {e}", exc_info=True)
        final_error_message = f"An unexpected error occurred: {e}"

    # Proceed with the result
    if hydrated_data:
        context.user_data[ORIGINAL_PARSED_DATA_KEY] = hydrated_data
        context.user_data[CURRENT_EDIT_DATA_KEY] = hydrated_data.copy()

        # ✅ --- (Protocol 1) Update DB Attempt (Success) ---
        parsing_repo.update_attempt(
            attempt_id=attempt_id,
            was_successful=True,
            result_data=_serialize_data_for_db(hydrated_data),
            parser_path_used=parser_path_used,
            latency_ms=latency_ms
            # ❌ REMOVED: template_id_used (ai-service no longer returns this)
        )
        db_session.commit()
        # --- End DB Logic ---

        channel_name = channel_info.get("title") if channel_info else "Unknown Channel"
        keyboard = build_editable_review_card(hydrated_data, channel_name=channel_name)
        await smart_safe_edit(
            context.bot, analyzing_message.chat.id, analyzing_message.message_id,
            text=f"📊 **Review Parsed Data**\n*Source:* `{channel_name}`\n\nPlease verify the data and choose an action:",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return AWAIT_REVIEW
    else:
        # ✅ --- (Protocol 1) Update DB Attempt (Failure) ---
        parsing_repo.update_attempt(
            attempt_id=attempt_id,
            was_successful=False,
            result_data={"error": final_error_message},
            parser_path_used=parser_path_used,
            latency_ms=latency_ms
        )
        db_session.commit()
        # --- End DB Logic ---
        
        escaped = html.escape(final_error_message)
        await smart_safe_edit(
            context.bot, analyzing_message.chat.id, analyzing_message.message_id,
            text=f"❌ **Analysis Failed**\n{escaped}",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END


# --- Entry Point 2: Image Forward (REFACTORED) ---
@uow_transaction
@require_active_user
async def forwarded_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    message = update.message
    if not message or not message.photo:
        return ConversationHandler.END

    if context.user_data.get(EDITING_FIELD_KEY) \
       or context.user_data.get('rec_creation_draft') \
       or context.user_data.get('awaiting_management_input'):
        log.debug("Forwarded photo ignored because another conversation is active.")
        return ConversationHandler.END

    clean_parsing_conversation_state(context)
    update_management_activity(context)

    # --- (Audit data capture remains the same) ---
    original_published_at = None
    channel_info = None
    if getattr(message, "forward_origin", None):
        forward_origin = message.forward_origin
        original_published_at = getattr(forward_origin, "date", None)
        origin_chat = getattr(forward_origin, "chat", None)
        if origin_chat:
            channel_info = {
                "id": getattr(origin_chat, "id", None),
                "title": getattr(origin_chat, "title", "Unknown Channel")
            }
    if not original_published_at:
        await message.reply_html("❌ **Error:** Please **forward** the original message, not a copy-paste.")
        clean_parsing_conversation_state(context)
        return ConversationHandler.END
        
    photo = message.photo[-1]
    file_id = photo.file_id
    user_db_id = db_user.id
    
    context.user_data[RAW_FORWARDED_TEXT_KEY] = f"image_file_id:{file_id}"
    context.user_data[FORWARD_AUDIT_DATA_KEY] = {
        "original_published_at": original_published_at.isoformat() if original_published_at else None,
        "channel_info": channel_info
    }
    # --- End Audit ---

    analyzing_message = await message.reply_text("⏳ Analyzing forwarded image (this may take a moment)...")
    context.user_data[ORIGINAL_MESSAGE_ID_KEY] = analyzing_message.message_id
    
    # ✅ --- (Protocol 1) NEW DB LOGIC ---
    parsing_repo = ParsingRepository(db_session)
    attempt = parsing_repo.add_attempt(
        user_id=user_db_id,
        raw_content=f"image_file_id:{file_id}",
        was_successful=False,
        parser_path_used="pending"
    )
    attempt_id = attempt.id
    context.user_data[PARSING_ATTEMPT_ID_KEY] = attempt_id
    db_session.commit()
    # --- End DB Logic ---

    hydrated_data = None
    parsing_result_json = None
    final_error_message = "Could not recognize a valid trade signal from the image."
    parser_path_used = "failed"
    latency_ms = 0

    try:
        # 1. Call the ImageParsingService (local service, not ai-service)
        img_parser_service = get_service(context, "image_parsing_service", ImageParsingService)
        # This service calls the /ai/parse_image endpoint
        parsing_result_json = await img_parser_service.parse_image_from_file_id(user_db_id, file_id)
        latency_ms = parsing_result_json.get("latency_ms", 0)
        parser_path_used = parsing_result_json.get("parser_path_used", "vision")

        # 2. Process the response
        if parsing_result_json.get("status") == "success" and parsing_result_json.get("data"):
            try:
                # ✅ Data comes as strings
                raw = parsing_result_json["data"]
                hydrated_data = {
                    "asset": raw.get("asset"),
                    "side": raw.get("side"),
                    "entry": parse_number(raw.get("entry")),
                    "stop_loss": parse_number(raw.get("stop_loss")),
                    "targets": parse_targets_list(
                        [f"{t.get('price')}@{t.get('close_percent')}" for t in raw.get("targets", [])]
                    )
                }
                # 3. Validate the hydrated data
                trade_service: TradeService = get_service(context, "trade_service", TradeService)
                trade_service._validate_recommendation_data(
                    hydrated_data['side'], hydrated_data['entry'],
                    hydrated_data['stop_loss'], hydrated_data['targets']
                )
            except (ValueError, TypeError) as e:
                log.warning(f"AI Service (Image) returned invalid data for attempt {attempt_id}: {e}")
                final_error_message = f"Analysis Failed: {e}"
                hydrated_data = None
            except Exception as e:
                log.error(f"Failed to re-hydrate JSON from AI service (Image): {e}")
                final_error_message = "Failed to process valid response from AI."
                hydrated_data = None
        else:
            final_error_message = parsing_result_json.get("error", "Unknown image analysis error.")

    except Exception as e:
        log.error(f"Critical error during image parsing: {e}", exc_info=True)
        final_error_message = f"An unexpected error occurred: {e}"

    # 5. Handle result
    if hydrated_data:
        context.user_data[ORIGINAL_PARSED_DATA_KEY] = hydrated_data
        context.user_data[CURRENT_EDIT_DATA_KEY] = hydrated_data.copy()

        # ✅ --- (Protocol 1) Update DB Attempt (Success) ---
        parsing_repo.update_attempt(
            attempt_id=attempt_id,
            was_successful=True,
            result_data=_serialize_data_for_db(hydrated_data),
            parser_path_used=parser_path_used,
            latency_ms=latency_ms
        )
        db_session.commit()
        # --- End DB Logic ---

        channel_name = channel_info.get("title") if channel_info else "Unknown Channel"
        keyboard = build_editable_review_card(hydrated_data, channel_name=channel_name)
        await smart_safe_edit(
            context.bot, analyzing_message.chat.id, analyzing_message.message_id,
            text=f"📊 **Review Parsed Image Data**\n*Source:* `{channel_name}`\n\nPlease verify the data and choose an action:",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return AWAIT_REVIEW
    else:
        # ✅ --- (Protocol 1) Update DB Attempt (Failure) ---
        parsing_repo.update_attempt(
            attempt_id=attempt_id,
            was_successful=False,
            result_data={"error": final_error_message},
            parser_path_used=parser_path_used,
            latency_ms=latency_ms
        )
        db_session.commit()
        # --- End DB Logic ---

        escaped = html.escape(final_error_message)
        await smart_safe_edit(
            context.bot, analyzing_message.chat.id, analyzing_message.message_id,
            text=f"❌ **Image Analysis Failed**\n{escaped}",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END


# --- ✅ ADDED: Local function to handle corrections (replaces /ai/record_correction) ---
async def _record_correction_local(
    db_session,
    attempt_id: int, 
    corrected_data: Dict[str, Any], 
    original_data: Optional[Dict[str, Any]]
):
    """Saves the correction diff to the DB locally."""
    if not attempt_id or original_data is None:
        log.warning("record_correction_local skipped: missing data.")
        return

    diff = {}
    keys = set(original_data.keys()) | set(corrected_data.keys())

    def norm(v):
        if isinstance(v, Decimal): return str(v)
        if isinstance(v, list):
            try:
                return sorted([(str(t['price']), t.get('close_percent', 0.0)) for t in v])
            except Exception: return v
        return v

    for k in keys:
        norm_old = norm(original_data.get(k))
        norm_new = norm(corrected_data.get(k))
        if norm_old != norm_new:
            diff[k] = {
                "old": _serialize_data_for_db(original_data).get(k), 
                "new": _serialize_data_for_db(corrected_data).get(k)
            }

    if not diff:
        log.info(f"No differences to record for correction on attempt {attempt_id}.")
        return

    try:
        parsing_repo = ParsingRepository(db_session)
        parsing_repo.update_attempt(
            attempt_id=attempt_id, 
            was_corrected=True, 
            corrections_diff=diff
        )
        # Commit the correction immediately
        db_session.commit()
        log.info(f"Recorded correction locally for attempt {attempt_id}")
    except Exception as e:
        log.error(f"Failed to record correction locally: {e}", exc_info=True)
        db_session.rollback()


# --- Shared Conversation Handlers (REFACTORED) ---
@uow_transaction
@require_active_user
async def review_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    if await handle_management_timeout(update, context):
        return ConversationHandler.END
    update_management_activity(context)

    # ... (State Recovery Logic) ...
    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    original_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)
    audit_data = context.user_data.get(FORWARD_AUDIT_DATA_KEY)
    if not (current_data and original_message_id and audit_data):
        # ... (Recovery logic) ...
        log.warning(f"State recovery failed for user {update.effective_user.id}. Ending conversation.")
        await smart_safe_edit(context.bot, query.message.chat.id, query.message.message_id,
                              text="❌ Session expired or data lost. Please forward again.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END


    if action in (CallbackAction.CONFIRM.value, CallbackAction.WATCH_CHANNEL.value):
        trade_service: TradeService = get_service(context, "trade_service", TradeService)
        attempt_id = context.user_data.get(PARSING_ATTEMPT_ID_KEY)
        original_data = context.user_data.get(ORIGINAL_PARSED_DATA_KEY)
        raw_text = context.user_data.get(RAW_FORWARDED_TEXT_KEY)
        was_corrected = (original_data != current_data)

        status_to_set = "PENDING_ACTIVATION" if action == CallbackAction.CONFIRM.value else "WATCHLIST"
        action_verb = "Activated" if status_to_set == "PENDING_ACTIVATION" else "Added to Watchlist"

        try:
            trade_service._validate_recommendation_data(
                current_data['side'], current_data['entry'],
                current_data['stop_loss'], current_data['targets']
            )
        except ValueError as e:
            error_text = f"❌ **Error saving trade:** {html.escape(str(e))}"
            await smart_safe_edit(context.bot, query.message.chat.id, original_message_id,
                               text=error_text, reply_markup=None, parse_mode=ParseMode.HTML)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

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
            await smart_safe_edit(context.bot, query.message.chat.id, original_message_id,
                                  text="❌ Error saving trade. Please try again later.", reply_markup=None)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

        # ✅ REFACTORED: Call local correction function (don't await, run in background)
        if attempt_id and was_corrected:
            log.debug(f"Recording correction for attempt {attempt_id} locally...")
            # We must pass the session, so we can't use create_task easily.
            # We'll run it synchronously but it's fast (local DB update).
            await _record_correction_local(db_session, attempt_id, current_data, original_data)
        
        if result.get('success'):
            success_msg = f"✅ **Trade #{result['trade_id']}** for **{result['asset']}** {action_verb} successfully!"
            await smart_safe_edit(context.bot, query.message.chat.id, original_message_id, text=success_msg, reply_markup=None)
            
            # (Template suggestion logic...)
            # We check if a template was used by checking the attempt_id
            template_used_initially = False
            if attempt_id:
                try:
                    attempt = db_session.get(ParsingAttempt, attempt_id)
                    if attempt:
                        template_used_initially = bool(attempt.used_template_id)
                except Exception as e:
                    log.error(f"Error checking template usage for suggestion on attempt {attempt_id}: {e}")

            if was_corrected and not template_used_initially:
                try:
                    from capitalguard.interfaces.telegram.keyboards import build_confirmation_keyboard
                    confirm_kb = build_confirmation_keyboard(
                        CallbackNamespace.SAVE_TEMPLATE, attempt_id,
                        confirm_text="💾 Yes, Save Format", cancel_text="🚫 No, Thanks"
                    )
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text="You corrected the parsed data.\nSave this message format as a personal template?",
                        reply_markup=confirm_kb
                    )
                except Exception:
                    log.exception("Failed to send template suggestion prompt")
            
            clean_parsing_conversation_state(context)
            return ConversationHandler.END
        else:
            error_text = f"❌ **Error saving trade:** {html.escape(result.get('error', 'Unknown'))}"
            await smart_safe_edit(context.bot, query.message.chat.id, original_message_id, text=error_text, reply_markup=None, parse_mode=ParseMode.HTML)
            clean_parsing_conversation_state(context)
            return ConversationHandler.END

    elif action == CallbackAction.EDIT_FIELD.value:
        # ... (This logic remains unchanged) ...
        if not params: return AWAIT_REVIEW
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
        # ... (This logic remains unchanged) ...
        await smart_safe_edit(context.bot, query.message.chat.id, original_message_id, text="❌ Operation cancelled.", reply_markup=None)
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    log.warning(f"Unhandled callback action in review state: {action} from data: {query.data}")
    return AWAIT_REVIEW


# --- (correction_value_handler remains the same) ---
@uow_transaction
@require_active_user
async def correction_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    # ... (This entire function's logic remains exactly the same as before) ...
    if await handle_management_timeout(update, context): return ConversationHandler.END
    update_management_activity(context)
    field_to_edit = context.user_data.get(EDITING_FIELD_KEY)
    current_data = context.user_data.get(CURRENT_EDIT_DATA_KEY)
    original_message_id = context.user_data.get(ORIGINAL_MESSAGE_ID_KEY)
    audit_data = context.user_data.get(FORWARD_AUDIT_DATA_KEY)
    user_input = update.message.text.strip() if update.message and update.message.text else ""
    try: await update.message.delete()
    except Exception: pass
    if not (field_to_edit and current_data and original_message_id and audit_data):
        log.warning(f"Correction value handler called with invalid state for user {update.effective_user.id}.")
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
            if side_upper in ["LONG", "SHORT"]:
                temp_data['side'] = side_upper
                validated = True
            else: raise ValueError("Side must be LONG or SHORT.")
        elif field_to_edit in ["entry", "stop_loss"]:
            price = parse_number(user_input)
            if price is None or price <= 0: raise ValueError("Invalid price format (must be > 0).")
            temp_data[field_to_edit] = price
            validated = True
        elif field_to_edit == "targets":
            tokens = re.split(r'[\s\n,]+', user_input) # Simple split
            targets = parse_targets_list(tokens)
            if not targets: raise ValueError("Invalid targets format or no valid targets found.")
            temp_data['targets'] = targets
            validated = True
        
        if validated:
            trade_service = get_service(context, "trade_service", TradeService)
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
            channel_name = audit_data.get("channel_info", {}).get("title", "Unknown Channel")
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
        clean_parsing_conversation_state(context)
        return ConversationHandler.END


# --- ✅ REFACTORED: Template suggestion now uses local DB access ---
@uow_transaction
@require_active_user
async def save_template_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()

    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    attempt_id = int(params[0]) if params and isinstance(params[0], str) and params[0].isdigit() else None

    try:
        await smart_safe_edit(context.bot, query.message.chat.id, query.message.message_id, text=query.message.text_html, reply_markup=None)
    except Exception: pass

    if action == CallbackAction.CONFIRM.value and attempt_id:
        log.debug(f"User confirmed saving template for attempt {attempt_id}. Saving locally.")
        try:
            # ✅ REFACTORED: Local DB logic
            parsing_repo = ParsingRepository(db_session)
            attempt = parsing_repo.session.get(ParsingAttempt, attempt_id)
            if not attempt:
                raise ValueError("Attempt ID not found.")
            if not attempt.was_corrected or attempt.user_id != db_user.id:
                raise ValueError("Invalid suggestion request (not corrected or wrong user).")

            template_name = f"User {db_user.id} Suggestion (Attempt {attempt_id})"
            raw_content_display = attempt.raw_content
            if raw_content_display.startswith("http") or raw_content_display.startswith("image_file_id:"):
                raw_content_display = f"[Image Content: {raw_content_display}]"

            pattern_placeholder = (
                f"# REVIEW NEEDED: Source Attempt ID {attempt.id}\n"
                f"# User ID: {db_user.id}\n"
                f"# Corrections:\n{json.dumps(attempt.corrections_diff, indent=2)}\n\n"
                f"# --- Original Content ---\n{raw_content_display}"
            )
            
            new_template = parsing_repo.add_template(
                name=template_name,
                pattern_type="regex_review_needed",
                pattern_value=pattern_placeholder,
                analyst_id=db_user.id,
                is_public=False,
                stats={"source_attempt_id": attempt.id}
            )
            db_session.commit() # Commit the new template
            # --- End local DB logic ---

            await query.message.reply_text(f"✅ Template suggestion (ID: {new_template.id}) submitted for review.")
        except Exception as e:
            log.error(f"Error saving template suggestion from attempt {attempt_id}: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Error submitting template suggestion: {e}")
            db_session.rollback()
    else:
        await query.message.reply_text("ℹ️ Template suggestion discarded.")

    return ConversationHandler.END


# --- (cancel_parsing_conversation remains the same) ---
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


# --- (register_forward_parsing_handlers remains the same) ---
def register_forward_parsing_handlers(app: Application):
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                forwarded_message_handler
            ),
            MessageHandler(
                filters.FORWARDED & filters.PHOTO & ~filters.COMMAND & filters.ChatType.PRIVATE,
                forwarded_photo_handler
            )
        ],
        states={
            AWAIT_REVIEW: [CallbackQueryHandler(
                review_callback_handler,
                pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:"
            )],
            AWAIT_CORRECTION_VALUE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                    correction_value_handler
                ),
                CallbackQueryHandler(
                    cancel_parsing_conversation, 
                    pattern=f"^{CallbackNamespace.FORWARD_PARSE.value}:{CallbackAction.CANCEL.value}:edit"
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_parsing_conversation),
            CallbackQueryHandler(cancel_parsing_conversation, pattern="^.*"),
            MessageHandler(filters.ALL & filters.ChatType.PRIVATE, cancel_parsing_conversation)
        ],
        name="unified_parsing_conversation",
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
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/forward_parsing_handler.py ---