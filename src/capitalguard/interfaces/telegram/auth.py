# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/auth.py ---
# File: src/capitalguard/interfaces/telegram/auth.py
# Version: v25.8.0-CRITICAL-FIX (Channel Post Crash Fix)
# ✅ THE FIX: Added checks for 'effective_user' to prevent AttributeError when
#             handling updates from Channels (where user is None).

import logging
from functools import wraps
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import User, UserType
from .keyboards import build_subscription_keyboard
from capitalguard.config import settings

log = logging.getLogger(__name__)


def get_db_user(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session) -> User:
    """
    A state-safe helper to retrieve a user object using a provided live session.
    It only caches the user's DB ID in the context for the duration of the update.
    """
    user = update.effective_user
    if not user:
        return None  # ✅ Safe return if no user (Channel context)

    if 'db_user_id' in context.user_data:
        user_id = context.user_data['db_user_id']
        db_user = UserRepository(db_session).find_by_id(user_id)
        if db_user:
            return db_user

    db_user = UserRepository(db_session).find_or_create(
        telegram_id=user.id,
        first_name=user.first_name,
        username=user.username
    )
    
    if db_user:
        context.user_data['db_user_id'] = db_user.id
    
    return db_user


def require_active_user(func: Callable) -> Callable:
    """
    Decorator for simple CommandHandlers. Checks if the user has an active account.
    It MUST be placed below @uow_transaction.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        db_session = kwargs.get('db_session')
        if not db_session:
            log.critical("FATAL: @require_active_user used without @uow_transaction. Cannot get a DB session.")
            raise RuntimeError("@require_active_user must be used with @uow_transaction.")

        # ✅ FIX: Check if user exists before accessing ID (Crucial for Channels)
        if not update.effective_user:
            # This is likely a channel post or system update; pass it through.
            # The handler downstream (like management_handlers) will decide what to do.
            return await func(update, context, *args, **kwargs)

        db_user = get_db_user(update, context, db_session)
        
        if not db_user or not db_user.is_active:
            # ✅ FIX: Use safe access to user ID for logging
            user_id = update.effective_user.id if update.effective_user else "Unknown"
            log.warning(f"Blocked access for inactive user {user_id}.")
            
            message = "🚫 <b>Access Denied</b>\nYour account is not active. Please contact support."
            if update.message:
                await update.message.reply_html(message)
            elif update.callback_query:
                await update.callback_query.answer("🚫 Access Denied: Account not active.", show_alert=True)
            return
        
        kwargs['db_user'] = db_user
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_analyst_user(func: Callable) -> Callable:
    """
    Decorator that checks if the user has the ANALYST role.
    Must be placed below @require_active_user.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        db_user = kwargs.get('db_user')
        
        # If db_user is None (e.g. Channel post passed through require_active_user),
        # block access to analyst commands.
        if not db_user or db_user.user_type != UserType.ANALYST:
            user_id = update.effective_user.id if update.effective_user else "Unknown"
            log.warning(f"Blocked analyst-only command for user {user_id}.")
            
            message = "🚫 <b>Permission Denied</b>\nThis command is available for analysts only."
            if update.message:
                await update.message.reply_html(message)
            elif update.callback_query:
                await update.callback_query.answer("🚫 Permission Denied: Analysts only.", show_alert=True)
            return
            
        return await func(update, context, *args, **kwargs)
    return wrapper


def require_channel_subscription(func: Callable) -> Callable:
    """Decorator that enforces channel subscription."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        channel_id_str = settings.TELEGRAM_CHAT_ID
        if not channel_id_str:
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
            message = "⚠️ <b>Subscription Required</b>\n\nTo use this bot, you must first be a member of our channel."
            
            if update.message:
                await update.message.reply_html(
                    text=message,
                    reply_markup=build_subscription_keyboard(channel_link),
                    disable_web_page_preview=True
                )
            elif update.callback_query:
                await update.callback_query.answer("Please subscribe to our main channel first.", show_alert=True)
            return
    return wrapper
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---