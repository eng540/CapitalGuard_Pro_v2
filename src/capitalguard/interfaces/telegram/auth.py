# src/capitalguard/interfaces/telegram/auth.py (v25.5 - FINAL & STATE-SAFE)
"""
Authentication and authorization decorators for Telegram handlers.
This version implements a stateless approach to handling user objects to prevent
DetachedInstanceError after persistence rehydration.
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

def get_db_user(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session) -> User:
    """
    A state-safe helper to retrieve a user object.
    It NEVER stores the ORM object in context.user_data. It only stores the ID.
    It relies on the calling handler (decorated with @uow_transaction) to provide a live session.
    """
    user = update.effective_user
    if not user:
        return None

    # Check if we have a cached ID from a previous run in the same update
    if 'db_user_id' in context.user_data:
        user_id = context.user_data['db_user_id']
        # Fetch the user with the CURRENT session
        return UserRepository(db_session).find_by_id(user_id)

    # If not cached, find or create the user
    db_user = UserRepository(db_session).find_or_create(
        telegram_id=user.id,
        first_name=user.first_name,
        username=user.username
    )
    
    if db_user:
        # Cache the ID for this update cycle, NOT the object
        context.user_data['db_user_id'] = db_user.id
    
    return db_user

def require_active_user(func: Callable) -> Callable:
    """
    Decorator that checks if the user has an active account.
    It MUST be used on a function that is already decorated with @uow_transaction.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # This assumes 'db_session' is passed by @uow_transaction
        db_session = kwargs.get('db_session')
        if not db_session:
            log.critical("FATAL: @require_active_user used without @uow_transaction. Cannot get a DB session.")
            raise RuntimeError("@require_active_user must be used with @uow_transaction.")

        db_user = get_db_user(update, context, db_session)
        
        if not db_user or not db_user.is_active:
            log.warning(f"Blocked access for inactive user {update.effective_user.id}.")
            message = "🚫 <b>Access Denied</b>\nYour account is not active. Please contact support."
            if update.message:
                await update.message.reply_html(message)
            elif update.callback_query:
                await update.callback_query.answer("🚫 Access Denied: Account not active.", show_alert=True)
            return
        
        # Pass the live, session-bound user object to the handler
        kwargs['db_user'] = db_user
        return await func(update, context, *args, **kwargs)
    return wrapper

def require_analyst_user(func: Callable) -> Callable:
    """
    Decorator that checks if the user has the ANALYST role.
    Must be used on a function decorated with @uow_transaction and @require_active_user.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # The db_user is passed in kwargs by the @require_active_user decorator
        db_user = kwargs.get('db_user')
        
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

# Note: require_channel_subscription does not need db_user, so it can remain as is.
def require_channel_subscription(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        channel_id_str = settings.TELEGRAM_CHAT_ID
        if not channel_id_str:
            return await func(update, context, *args, **kwargs)

        user = update.effective_user
        if not user: return

        try:
            member = await context.bot.get_chat_member(chat_id=channel_id_str, user_id=user.id)
            if member.status in ['creator', 'administrator', 'member', 'restricted']:
                return await func(update, context, *args, **kwargs)
            else:
                raise ValueError(f"User is not a member, status: {member.status}")
        except Exception:
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

#END