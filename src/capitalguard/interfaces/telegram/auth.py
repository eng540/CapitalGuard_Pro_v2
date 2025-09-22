# --- START OF FINAL, COMPLETE, AND ROBUST FILE (Version 13.1.0) ---
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
    A DB-backed filter that checks if a user exists and is active.
    This is the primary mechanism for controlling access for subscribers.
    It creates a user record if one doesn't exist.
    """
    def __init__(self) -> None:
        super().__init__(name="Access_Control_Filter")

    def filter(self, update: Update) -> bool:
        """
        Checks for an active user in the database.
        Note: This synchronous method should NOT send asynchronous messages.
        It only returns True or False. The denial message is handled by decorators.
        """
        if not update or not getattr(update, "effective_user", None):
            return False

        u = update.effective_user
        tg_id = u.id

        try:
            with SessionLocal() as session:
                user_repo = UserRepository(session)
                # Ensure user exists, creating them as inactive if new.
                user = user_repo.find_or_create(
                    telegram_id=tg_id,
                    first_name=getattr(u, "first_name", None),
                )
                # The core authorization logic: only allow active users.
                if user and user.is_active:
                    return True
        except Exception as e:
            log.error(f"Access_Control_Filter: Database error for user {tg_id}: {e}", exc_info=True)
        
        # If user is not active or an error occurred, block access.
        return False

# A single instance of the filter for use in ConversationHandler entry_points if needed.
ALLOWED_USER_FILTER = _AccessControlFilter()


def require_active_user(handler_func: Callable) -> Callable:
    """
    Decorator that checks if the user is active. If not, it sends a denial
    message and stops execution. This should be the first decorator.
    """
    @functools.wraps(handler_func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if ALLOWED_USER_FILTER.filter(update):
            return await handler_func(update, context, *args, **kwargs)
        
        user = update.effective_user
        log.warning(f"Blocked access for inactive user {user.id} ({user.username}) to a protected command.")
        
        # Send a clear denial message.
        await update.message.reply_html(
            "🚫 <b>Access Restricted</b>\n\n"
            "Your account is not active. This is a premium bot for subscribers only.\n\n"
            "Please contact the administrator for access."
        )
        return
        
    return wrapper


def require_channel_subscription(handler_func: Callable) -> Callable:
    """
    A decorator that enforces channel subscription before executing a command handler.
    This should be placed *after* @require_active_user.
    """
    @functools.wraps(handler_func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        channel_id_str = settings.TELEGRAM_CHAT_ID
        if not channel_id_str:
            log.error("TELEGRAM_CHAT_ID is not set. Cannot check for channel subscription. Skipping check.")
            return await handler_func(update, context, *args, **kwargs)

        user = update.effective_user
        if not user:
            return

        try:
            member = await context.bot.get_chat_member(chat_id=channel_id_str, user_id=user.id)
            if member.status in ['creator', 'administrator', 'member']:
                return await handler_func(update, context, *args, **kwargs)
            else:
                raise ValueError("User is not a member.")
        except Exception:
            log.info(f"User {user.id} blocked from command '{update.message.text}' due to not being in channel {channel_id_str}.")
            
            channel_link = None
            channel_title = "our official channel"
            try:
                chat = await context.bot.get_chat(channel_id_str)
                channel_title = chat.title
                if chat.invite_link:
                    channel_link = chat.invite_link
                elif chat.username:
                    channel_link = f"https://t.me/{chat.username}"
            except Exception as e:
                log.error(f"Could not fetch details for channel {channel_id_str}: {e}")

            message = (
                f"⚠️ <b>Subscription Required</b>\n\n"
                f"To use this bot, you must first be a member of our channel: <b>{channel_title}</b>.\n\n"
                f"Please join and then try your command again."
            )
            await update.message.reply_html(
                text=message,
                reply_markup=build_subscription_keyboard(channel_link),
                disable_web_page_preview=True
            )
            return

    return wrapper

# --- END OF FINAL, COMPLETE, AND ROBUST FILE ---