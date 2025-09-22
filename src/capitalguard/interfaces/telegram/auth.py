# --- START OF FINAL, COMPLETE, AND LOGIC-CORRECTED FILE (Version 13.2.0) ---
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

class _EnsureUserFilter(BaseFilter):
    """
    A fundamental filter that runs for almost all user interactions.
    Its SOLE responsibility is to ensure that a user record exists in the database.
    It creates a user as inactive if they don't exist.
    """
    def __init__(self) -> None:
        super().__init__(name="Ensure_User_Filter")

    def filter(self, update: Update) -> bool:
        if not update or not getattr(update, "effective_user", None):
            return False

        u = update.effective_user
        tg_id = u.id

        try:
            with SessionLocal() as session:
                user_repo = UserRepository(session)
                user_repo.find_or_create(
                    telegram_id=tg_id,
                    first_name=getattr(u, "first_name", None),
                )
            return True # Always pass, its job is just to create.
        except Exception as e:
            log.error(f"EnsureUserFilter: Database error for user {tg_id}: {e}", exc_info=True)
            return False # Block if DB fails

class _AccessControlFilter(BaseFilter):
    """
    A DB-backed filter that checks if a user is marked as `is_active=True`.
    It ASSUMES the user record already exists (thanks to EnsureUserFilter).
    """
    def __init__(self) -> None:
        super().__init__(name="Access_Control_Filter")

    def filter(self, update: Update) -> bool:
        if not update or not getattr(update, "effective_user", None):
            return False

        u = update.effective_user
        tg_id = u.id

        try:
            with SessionLocal() as session:
                user_repo = UserRepository(session)
                user = user_repo.find_by_telegram_id(tg_id)
                if user and user.is_active:
                    return True
        except Exception as e:
            log.error(f"AccessControlFilter: Database error for user {tg_id}: {e}", exc_info=True)
        
        return False

# Create instances of the filters
ENSURE_USER_FILTER = _EnsureUserFilter()
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
        
        contact_info = "the administrator"
        if settings.ADMIN_CONTACT:
            contact_info = f"<b>{settings.ADMIN_CONTACT}</b>"

        message = (
            "🚫 <b>Access Restricted</b>\n\n"
            "Your account is not active. This is a premium bot for subscribers only.\n\n"
            f"Please contact {contact_info} for access."
        )
        
        if update.message:
            await update.message.reply_html(message)
        elif update.callback_query:
            await update.callback_query.answer(
                "🚫 Access Restricted. Please contact support.",
                show_alert=True
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
            log.info(f"User {user.id} blocked from command '{getattr(update.message, 'text', 'N/A')}' due to not being in channel {channel_id_str}.")
            
            channel_link = settings.TELEGRAM_CHANNEL_INVITE_LINK
            channel_title = "our official channel"
            
            if not channel_link:
                log.critical("TELEGRAM_CHANNEL_INVITE_LINK is not set! Cannot show join button to user.")
            else:
                try:
                    chat = await context.bot.get_chat(channel_id_str)
                    channel_title = chat.title
                except Exception:
                    pass

            message = (
                f"⚠️ <b>Subscription Required</b>\n\n"
                f"To use this bot, you must first be a member of our channel: <b>{channel_title}</b>.\n\n"
                f"Please join and then try your command again."
            )
            
            if update.message:
                await update.message.reply_html(
                    text=message,
                    reply_markup=build_subscription_keyboard(channel_link),
                    disable_web_page_preview=True
                )
            elif update.callback_query:
                await update.callback_query.answer(
                    "Please subscribe to our main channel first.",
                    show_alert=True
                )
            return

    return wrapper

# --- END OF FINAL, COMPLETE, AND LOGIC-CORRECTED FILE ---