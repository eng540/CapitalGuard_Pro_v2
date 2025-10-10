# src/capitalguard/interfaces/telegram/channel_linking_handler.py (v1.1 - COMPLETE, FINAL & ROBUST)
"""
Handles the conversation flow for linking an analyst's Telegram channel.

This version includes a robust check for channel identification that reliably
works for all channel types (public, private, supergroups) by inspecting the
structure of the chat ID, instead of the potentially ambiguous `type` attribute.

This is a complete, final, and production-ready file.
"""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

from capitalguard.infrastructure.db.uow import uow_transaction
from .auth import require_active_user, require_analyst_user
from capitalguard.infrastructure.db.repository import ChannelRepository

log = logging.getLogger(__name__)

# --- Conversation State ---
AWAITING_CHANNEL_FORWARD = 1

# --- Conversation Entry Point ---

@uow_transaction
@require_active_user
@require_analyst_user
async def link_channel_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """
    Starts the channel linking conversation and asks the user to forward a message.
    """
    await update.message.reply_html(
        "<b>🔗 Link a New Channel</b>\n\n"
        "To link a channel where the bot can publish signals, please do the following:\n"
        "1. Add this bot as an administrator to your channel with 'Post Messages' permission.\n"
        "2. Forward any message from that channel to this chat.\n\n"
        "To cancel this process at any time, type /cancel."
    )
    return AWAITING_CHANNEL_FORWARD

# --- State Handler ---

async def _bot_has_post_rights(context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> bool:
    """
    Verifies that the bot can send and delete messages in the target channel.
    This is a crucial step to ensure publishing will work.
    """
    try:
        sent_message = await context.bot.send_message(
            chat_id=channel_id,
            text="✅ Verifying bot permissions... This message will be deleted automatically."
        )
        await context.bot.delete_message(
            chat_id=channel_id,
            message_id=sent_message.message_id
        )
        return True
    except Exception as e:
        log.warning(f"Bot permission check failed for channel {channel_id}: {e}")
        return False

@uow_transaction
@require_active_user
@require_analyst_user
async def received_channel_forward(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """
    Handles the forwarded message within the conversation. It validates the message,
    verifies permissions, and links the channel if successful.
    """
    msg = update.message
    forwarded_from_chat = getattr(msg, "forward_from_chat", None)
    
    # ✅ THE FIX: Use a robust method to identify a channel/supergroup.
    # All channel/supergroup IDs are negative and start with '-100'. This is the most
    # reliable way to check, regardless of the `type` attribute.
    is_from_channel = forwarded_from_chat and str(getattr(forwarded_from_chat, "id", 0)).startswith("-100")

    if not is_from_channel:
        await msg.reply_text(
            "❌ That does not appear to be a message from a channel. "
            "Please forward a message from the channel you wish to link, or type /cancel."
        )
        return AWAITING_CHANNEL_FORWARD

    chat_id, title, username = int(forwarded_from_chat.id), forwarded_from_chat.title, forwarded_from_chat.username

    repo = ChannelRepository(db_session)
    if repo.find_by_telegram_id_and_analyst(channel_id=chat_id, analyst_id=db_user.id):
        await msg.reply_html(f"☑️ Channel <b>{title}</b> is already linked to your account.")
        return ConversationHandler.END

    await msg.reply_html(f"⏳ Verifying permissions for channel '<b>{title}</b>'...", parse_mode='HTML')
    
    if not await _bot_has_post_rights(context, chat_id):
        await msg.reply_html(
            "❌ Permission check failed. Please ensure the bot is an administrator in the "
            f"channel '<b>{title}</b>' and has the 'Post Messages' permission. Then, try forwarding the message again."
        )
        return AWAITING_CHANNEL_FORWARD

    repo.add(analyst_id=db_user.id, telegram_channel_id=chat_id, username=username, title=title)
    
    uname_disp = f"(@{username})" if username else "(Private Channel)"
    await msg.reply_html(f"✅ Channel successfully linked: <b>{title or 'Untitled'}</b> {uname_disp}\nID: <code>{chat_id}</code>")
    
    return ConversationHandler.END

# --- Conversation Fallback ---

async def cancel_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Provides a clean exit from the conversation."""
    await update.message.reply_text("Channel linking process has been cancelled.")
    return ConversationHandler.END

# --- Registration ---

def register_channel_linking_handler(app: Application):
    """Creates and registers the ConversationHandler for channel linking."""
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("link_channel", link_channel_entry)],
        states={
            AWAITING_CHANNEL_FORWARD: [
                MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, received_channel_forward)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_link_handler)],
        name="channel_linking_conversation",
        per_user=True,
        per_chat=True,
    )
    app.add_handler(conv_handler)