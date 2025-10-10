# src/capitalguard/interfaces/telegram/channel_linking_handler.py (v1.0 - COMPLETE, FINAL & PRODUCTION-READY)
"""
Handles the conversation flow for linking an analyst's Telegram channel.

This feature is implemented as a self-contained ConversationHandler to ensure
its state is managed independently and does not conflict with other bot features.
The flow is explicit:
1. User runs /link_channel.
2. Bot enters a state awaiting a forwarded message.
3. Upon receiving a forwarded message from a channel, it verifies permissions.
4. On success, it links the channel and the conversation ends.
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
        # Send a temporary message to the channel
        sent_message = await context.bot.send_message(
            chat_id=channel_id,
            text="✅ Verifying bot permissions... This message will be deleted automatically."
        )
        # Immediately delete the message to keep the channel clean
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
    
    # 1. Validate that the forwarded message is from a channel
    if not forwarded_from_chat or getattr(forwarded_from_chat, "type", None) != "channel":
        await msg.reply_text(
            "❌ That does not appear to be a message from a channel. "
            "Please forward a message from the channel you wish to link, or type /cancel."
        )
        return AWAITING_CHANNEL_FORWARD  # Remain in the same state, waiting for a valid forward

    chat_id, title, username = int(forwarded_from_chat.id), forwarded_from_chat.title, forwarded_from_chat.username

    # 2. Check if this channel is already linked to this user
    repo = ChannelRepository(db_session)
    if repo.find_by_telegram_id_and_analyst(channel_id=chat_id, analyst_id=db_user.id):
        await msg.reply_html(f"☑️ Channel <b>{title}</b> is already linked to your account.")
        return ConversationHandler.END

    await msg.reply_text(f"⏳ Verifying permissions for channel '<b>{title}</b>'...", parse_mode='HTML')
    
    # 3. Verify bot permissions
    if not await _bot_has_post_rights(context, chat_id):
        await msg.reply_html(
            "❌ Permission check failed. Please ensure the bot is an administrator in the "
            f"channel '<b>{title}</b>' and has the 'Post Messages' permission. Then, try forwarding the message again."
        )
        return AWAITING_CHANNEL_FORWARD # Remain in state, allow user to fix permissions and retry

    # 4. Link the channel
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