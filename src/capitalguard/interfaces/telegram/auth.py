# src/capitalguard/interfaces/telegram/auth.py (v12.0 - Final Role-Based)
import logging
from functools import wraps
from typing import Optional, Callable

from telegram import Update
from telegram.ext import ContextTypes

from capitalguard.config import settings
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.models import User, UserType
from .keyboards import build_subscription_keyboard

log = logging.getLogger(__name__)

def _get_db_user(telegram_id: int) -> Optional[User]:
    with SessionLocal() as session:
        return UserRepository(session).find_by_telegram_id(telegram_id)

def require_active_user(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user:
            log.debug("Ignoring update with no effective_user.")
            return
        
        db_user = await context.application.create_task(_get_db_user, user.id)
        
        if not db_user: # Create user if they don't exist
            with SessionLocal() as session:
                db_user = UserRepository(session).find_or_create(user.id, first_name=user.first_name, username=user.username)

        if not db_user.is_active:
            log.warning(f"Blocked access for inactive user {user.id} ({user.username}).")
            message = "🚫 <b>Access Denied</b>\nYour account is not active. Please contact support."
            if update.message:
                await update.message.reply_html(message)
            elif update.callback_query:
                await update.callback_query.answer("🚫 Access Denied: Account not active.", show_alert=True)
            return
        
        context.user_data['db_user'] = db_user
        return await func(update, context, *args, **kwargs)
    return wrapper

def require_analyst_user(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        db_user = context.user_data.get('db_user')
        if not db_user or db_user.user_type != UserType.ANALYST:
            log.warning(f"Blocked analyst command for non-analyst user {update.effective_user.id}.")
            message = "🚫 <b>Permission Denied</b>\nThis command is available for analysts only."
            if update.message:
                await update.message.reply_html(message)
            elif update.callback_query:
                await update.callback_query.answer("🚫 Permission Denied: Analysts only.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper