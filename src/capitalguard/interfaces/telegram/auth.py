# src/capitalguard/interfaces/telegram/auth.py (v25.0 - FINAL & SIMPLIFIED)
"""
Authentication and authorization decorators for Telegram handlers.
This version simplifies the logic and ensures user records are always present.
"""

import logging
from functools import wraps
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import User, UserType
from .keyboards import build_subscription_keyboard
from capitalguard.config import settings

log = logging.getLogger(__name__)

def get_db_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User:
    """
    A helper function that ensures a user record exists and attaches it to the context.
    This should be called at the beginning of most handlers.
    """
    if 'db_user' in context.user_data:
        return context.user_data['db_user']

    user = update.effective_user
    if not user:
        return None

    with session_scope() as session:
        db_user = UserRepository(session).find_or_create(
            telegram_id=user.id,
            first_name=user.first_name,
            username=user.username
        )
        context.user_data['db_user'] = db_user
        return db_user

def require_active_user(func: Callable) -> Callable:
    """
    Decorator that checks if the user has an active account.
    It must be placed *after* @unit_of_work if both are used.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        db_user = get_db_user(update, context)
        
        if not db_user or not db_user.is_active:
            log.warning(f"Blocked access for inactive user {update.effective_user.id}.")
            message = "🚫 <b>Access Denied</b>\nYour account is not active. Please contact support."
            if update.message:
                await update.message.reply_html(message)
            elif update.callback_query:
                await update.callback_query.answer("🚫 Access Denied: Account not active.", show_alert=True)
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper

def require_analyst_user(func: Callable) -> Callable:
    """
    Decorator that checks if the user has the ANALYST role.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        db_user = get_db_user(update, context)
        
        if not db_user or db_user.user_type != UserType.ANALYST:
            log.warning(f"Blocked analyst-only command for non-analyst user {update.effective_user.id}.")
            message = "🚫 <b>Permission Denied</b>\nThis command is available for analysts only."
            if update.message:
                await update.message.reply_html(message)
            elif update.callback_query:
                await update.callback_query.answer("🚫 Permission Denied: Analysts only.", show_alert=True)
            return
            
        return await func(update, context, *args, **kwargs)
    return wrapper

def require_channel_subscription(func: Callable) -> Callable:
    """
    Decorator that enforces channel subscription before executing a command.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        channel_id_str = settings.TELEGRAM_CHAT_ID
        if not channel_id_str:
            log.warning("TELEGRAM_CHAT_ID is not set. Skipping channel subscription check.")
            return await func(update, context, *args, **kwargs)

        user = update.effective_user
        if not user:
            return

        try:
            member = await context.bot.get_chat_member(chat_id=channel_id_str, user_id=user.id)
            if member.status in ['creator', 'administrator', 'member', 'restricted']:
                return await func(update, context, *args, **kwargs)
            else:
                raise ValueError(f"User is not a member, status: {member.status}")
        except Exception:
            log.info(f"User {user.id} blocked from command due to not being in channel {channel_id_str}.")
            
            channel_link = settings.TELEGRAM_CHANNEL_INVITE_LINK
            channel_title = "our official channel"
            
            message = (
                f"⚠️ <b>Subscription Required</b>\n\n"
                f"To use this bot, you must first be a member of <b>{channel_title}</b>.\n\n"
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