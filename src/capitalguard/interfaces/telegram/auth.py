# --- START OF FINAL, COMPLETE, AND MONETIZATION-READY FILE (Version 13.0.0) ---
# src/capitalguard/interfaces/telegram/auth.py

import logging
import functools
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes
from telegram.ext.filters import BaseFilter

from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.base import SessionLocal
from .keyboards import build_subscription_keyboard
from capitalguard.config import settings

log = logging.getLogger(__name__)

class _AccessControlFilter(BaseFilter):
    """
    A robust, DB-backed authentication and authorization filter.

    This filter performs two crucial checks:
    1.  Ensures a user record exists in the database. New users are created
        with `is_active=False` by default.
    2.  Checks if the user is authorized to use the bot by verifying `user.is_active == True`.
    
    This is the primary mechanism for controlling access for subscribers.
    """

    def __init__(self) -> None:
        super().__init__(name="Access_Control_Filter")

    def filter(self, update: Update) -> bool:
        """
        This method is called by PTB for each incoming update.
        It checks for an active user and blocks access if the user is not active.
        """
        if not update or not getattr(update, "effective_user", None):
            return False

        u = update.effective_user
        tg_id = u.id

        try:
            with SessionLocal() as session:
                user_repo = UserRepository(session)
                user = user_repo.find_or_create(
                    telegram_id=tg_id,
                    first_name=getattr(u, "first_name", None),
                )
                # The core authorization logic: only allow active users.
                if user and user.is_active:
                    return True
                
                # If user is not active, send a denial message.
                # We do this outside the main flow to avoid blocking other handlers.
                # A simple way is to store a flag in context.
                if update.callback_query:
                    # For button clicks, it's better to answer the query.
                    asyncio.create_task(update.callback_query.answer("🚫 Access Restricted. Please contact support.", show_alert=True))
                else:
                    # For messages, we can send a reply.
                    denial_message = "🚫 *Access Restricted*\n\nThis is a premium bot for subscribers only. Please contact support to get access."
                    asyncio.create_task(update.message.reply_markdown(denial_message))

                return False
        except Exception as e:
            log.error(f"Access_Control_Filter: Database error for user {tg_id}: {e}", exc_info=True)
            return False

# Create a single instance of the filter to be used throughout the application.
ALLOWED_USER_FILTER = _AccessControlFilter()


def require_channel_subscription(handler_func: Callable) -> Callable:
    """
    A decorator that enforces channel subscription before executing a command handler.
    This is used for growth hacking and ensuring a user community.
    """
    @functools.wraps(handler_func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # The channel ID must be set in the environment variables.
        channel_id = settings.TELEGRAM_CHAT_ID
        if not channel_id:
            log.error("TELEGRAM_CHAT_ID is not set. Cannot check for channel subscription.")
            # Fail open: if not configured, allow the command to run.
            return await handler_func(update, context, *args, **kwargs)

        user = update.effective_user
        if not user:
            return

        try:
            member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user.id)
            if member.status in ['creator', 'administrator', 'member']:
                # User is in the channel, proceed with the original handler.
                return await handler_func(update, context, *args, **kwargs)
            else:
                # User is not in the channel.
                raise ValueError("User is not a member.")
        except Exception:
            # This block catches both API errors and the ValueError from above.
            log.info(f"User {user.id} blocked from command '{update.message.text}' due to not being in channel {channel_id}.")
            channel_username = "our official channel" # A default name
            # Try to get the actual channel link for a better user experience
            try:
                chat = await context.bot.get_chat(channel_id)
                if chat.invite_link:
                    channel_username = f"[{chat.title}]({chat.invite_link})"
                elif chat.username:
                    channel_username = f"@{chat.username}"
            except Exception:
                pass
            
            message = (
                f"⚠️ *Access Denied*\n\n"
                f"To use this command, you must first be a member of {channel_username}.\n\n"
                f"Please join the channel and then try your command again."
            )
            await update.message.reply_markdown(
                text=message,
                reply_markup=build_subscription_keyboard(context),
                disable_web_page_preview=True
            )
            return # Stop further execution

    return wrapper

# --- END OF FINAL, COMPLETE, AND MONETIZATION-READY FILE ---