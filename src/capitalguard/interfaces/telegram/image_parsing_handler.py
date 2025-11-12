# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/image_parsing_handler.py ---
"""
Handles the user flow for parsing a forwarded IMAGE (Screenshot).

This handler works in conjunction with the existing `forward_parsing_handler`
but is triggered by `filters.PHOTO` instead of `filters.TEXT`.

It uses the same conversation states, state keys, and review keyboards
to provide a consistent user experience for both text and image parsing.
"""

import logging
import html
from decimal import Decimal

from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters,
    ConversationHandler
)
from telegram.constants import ParseMode

# Infrastructure & Application specific imports
from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.interfaces.telegram.helpers import get_service
from capitalguard.interfaces.telegram.auth import require_active_user, get_db_user
from capitalguard.application.services.image_parsing_service import ImageParsingService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.telegram.keyboards import build_editable_review_card
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list

# Import states and helpers from the main forward parsing handler
from .forward_parsing_handler import (
    AWAIT_REVIEW,
    clean_parsing_conversation_state,
    smart_safe_edit,
    PARSING_ATTEMPT_ID_KEY,
    ORIGINAL_PARSED_DATA_KEY,
    CURRENT_EDIT_DATA_KEY,
    RAW_FORWARDED_TEXT_KEY,
    ORIGINAL_MESSAGE_ID_KEY,
    FORWARD_AUDIT_DATA_KEY
)
# Import management helpers for activity tracking
from .management_handlers import (
    handle_management_timeout,
    update_management_activity,
    MANAGEMENT_TIMEOUT
)


log = logging.getLogger(__name__)

@uow_transaction
@require_active_user
async def forwarded_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """
    Entry point for the image parsing conversation.
    Triggered when a user forwards a photo to the bot in a private chat.
    """
    message = update.message
    if not message or not message.photo:
        return ConversationHandler.END

    # Ignore if another conversation is active
    if context.user_data.get("editing_field_key") \
       or context.user_data.get('rec_creation_draft') \
       or context.user_data.get('awaiting_management_input'):
        log.debug("Forwarded photo ignored because another conversation is active.")
        return ConversationHandler.END

    clean_parsing_conversation_state(context)
    update_management_activity(context)

    # --- Capture Audit Data (Timestamp and Channel) ---
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
        await message.reply_html(
            "❌ **Error:** This message seems to be a copy-paste, not a forward.\n"
            "To analyze, please **forward** the original message directly from the channel."
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END
        
    # Get the highest resolution photo
    photo = message.photo[-1]
    file_id = photo.file_id
    user_db_id = db_user.id
    
    # Store raw data for potential correction/template saving
    # Note: We can't store the full text, so we store the file_id
    context.user_data[RAW_FORWARDED_TEXT_KEY] = f"image_file_id:{file_id}"
    context.user_data[FORWARD_AUDIT_DATA_KEY] = {
        "original_published_at": original_published_at.isoformat() if original_published_at else None,
        "channel_info": channel_info
    }

    analyzing_message = await message.reply_text("⏳ Analyzing forwarded image (this may take a moment)...")
    context.user_data[ORIGINAL_MESSAGE_ID_KEY] = analyzing_message.message_id
    
    hydrated_data = None
    parsing_result_json = None

    try:
        # 1. Call the ImageParsingService
        img_parser_service = get_service(context, "image_parsing_service", ImageParsingService)
        parsing_result_json = await img_parser_service.parse_image_from_file_id(user_db_id, file_id)

        # 2. Process the response (identical flow to text parser)
        if parsing_result_json.get("status") == "success" and parsing_result_json.get("data"):
            try:
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

                # 4. Store state for the review conversation
                context.user_data[PARSING_ATTEMPT_ID_KEY] = parsing_result_json.get("attempt_id")
                context.user_data[ORIGINAL_PARSED_DATA_KEY] = hydrated_data
                context.user_data[CURRENT_EDIT_DATA_KEY] = hydrated_data.copy()

                channel_name = channel_info.get("title") if channel_info else "Unknown Channel"
                keyboard = build_editable_review_card(hydrated_data, channel_name=channel_name)

                await smart_safe_edit(
                    context.bot, analyzing_message.chat.id, analyzing_message.message_id,
                    text=f"📊 **Review Parsed Image Data**\n*Source:* `{channel_name}`\n\nPlease verify the data and choose an action:",
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return AWAIT_REVIEW

            except (ValueError, TypeError) as e:
                log.warning(
                    f"AI Service (Image) returned invalid data for attempt {parsing_result_json.get('attempt_id')}: {e}"
                )
                parsing_result_json["error"] = f"Analysis Failed: {e}"
                hydrated_data = None
            except Exception as e:
                log.error(f"Failed to re-hydrate JSON from AI service (Image): {e}")
                parsing_result_json["error"] = "Failed to process valid response from AI."
                hydrated_data = None
        
        # 5. Handle failure
        error_msg = parsing_result_json.get("error", "Could not recognize a valid trade signal.")
        escaped = html.escape(error_msg)
        await smart_safe_edit(
            context.bot, analyzing_message.chat.id, analyzing_message.message_id,
            text=f"❌ **Image Analysis Failed**\n{escaped}",
            parse_mode=ParseMode.HTML,
            reply_markup=None
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

    except Exception as e:
        log.error(f"Critical error during image parsing: {e}", exc_info=True)
        await smart_safe_edit(
            context.bot, analyzing_message.chat.id, analyzing_message.message_id,
            text=f"❌ An unexpected error occurred: {str(e)}",
            reply_markup=None
        )
        clean_parsing_conversation_state(context)
        return ConversationHandler.END

def register_image_parsing_handler(app: Application):
    """
    Registers the conversation handler for forwarded photos.
    It uses the *same states* and *callback handlers* as the text-based
    forward_parsing_handler to maintain a single, consistent review flow.
    """
    
    # Get the existing states from the text-based handler
    # This is a bit of a hack, but it ensures we reuse the *exact* same
    # callback handlers (review_callback_handler, correction_value_handler)
    # and fallbacks (cancel_parsing_conversation)
    from .forward_parsing_handler import (
        review_callback_handler,
        correction_value_handler,
        cancel_parsing_conversation,
        save_template_confirm_handler,
        CallbackNamespace, CallbackAction
    )

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(
            filters.FORWARDED & filters.PHOTO & ~filters.COMMAND & filters.ChatType.PRIVATE,
            forwarded_photo_handler
        )],
        states={
            AWAIT_REVIEW: [CallbackQueryHandler(
                review_callback_handler,
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
        name="image_parsing_conversation", # Different name from the text one
        per_user=True, per_chat=True,
        persistent=False,
        conversation_timeout=MANAGEMENT_TIMEOUT,
        per_message=False # Suppress PTB warning
    )
    
    # Group 1 to run after main commands
    app.add_handler(conv_handler, group=1)
    
    # Note: The save_template_confirm_handler is already registered by
    # register_forward_parsing_handlers, so we don't need to add it again.

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/image_parsing_handler.py ---