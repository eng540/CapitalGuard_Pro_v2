# src/capitalguard/interfaces/telegram/auth.py (v12.2 - Async Hotfix)
import logging
from functools import wraps
from typing import Optional, Callable
import asyncio

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
        
        # ✅ ASYNC FIX: Use run_in_executor to correctly run sync DB code in an async context.
        loop = asyncio.get_running_loop()
        db_user = await loop.run_in_executor(None, _get_db_user, user.id)
        
        if not db_user:
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
        db_user: Optional[User] = context.user_data.get('db_user')
        
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