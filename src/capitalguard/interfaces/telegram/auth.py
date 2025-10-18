# src/capitalguard/interfaces/telegram/auth.py (v25.9 - ASYNC FIXED)
"""
مصادقة والتفويض والديكوراتورات والمساعدات لمعالجات Telegram.
هذا الإصدار يصلح مشكلة 'coroutine' object is not callable بشكل نهائي.
"""

import logging
import inspect
from functools import wraps
from typing import Callable, Any

from telegram import Update
from telegram.ext import ContextTypes

from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import User, UserType
from .keyboards import build_subscription_keyboard
from capitalguard.config import settings

log = logging.getLogger(__name__)

def get_db_user(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session) -> User:
    """
    مساعد آمن للحالة لاسترداد كائن مستخدم باستخدام جلسة حية مقدمة.
    """
    user = update.effective_user
    if not user:
        return None

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
    ديكوراتور لـ CommandHandlers البسيطة. يتحقق مما إذا كان للمستخدم حساب نشط.
    الإصدار المصحح: يتعامل بشكل صحيح مع الدوال المتزامنة وغير المتزامنة.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # البحث عن update و context في الوسائط
        update = None
        context = None
        db_session = None
        
        for arg in args:
            if hasattr(arg, 'effective_user'):  # Update object
                update = arg
            elif hasattr(arg, 'bot'):  # Context object
                context = arg
        
        # الحصول على db_session من kwargs
        db_session = kwargs.get('db_session')
        if not db_session:
            log.critical("FATAL: @require_active_user used without @uow_transaction. Cannot get a DB session.")
            if update and update.message:
                if inspect.iscoroutinefunction(func):
                    async def async_error():
                        await update.message.reply_html("🚫 <b>System Error</b>\nDatabase session not available.")
                    return async_error()
                else:
                    update.message.reply_html("🚫 <b>System Error</b>\nDatabase session not available.")
                    return None
            return None

        db_user = get_db_user(update, context, db_session)
        
        if not db_user or not db_user.is_active:
            log.warning(f"Blocked access for inactive user {update.effective_user.id if update else 'unknown'}.")
            message = "🚫 <b>Access Denied</b>\nYour account is not active. Please contact support."
            
            if update:
                if update.message:
                    if inspect.iscoroutinefunction(func):
                        async def async_reply():
                            await update.message.reply_html(message)
                        return async_reply()
                    else:
                        update.message.reply_html(message)
                elif update.callback_query:
                    if inspect.iscoroutinefunction(func):
                        async def async_answer():
                            await update.callback_query.answer("🚫 Access Denied: Account not active.", show_alert=True)
                        return async_answer()
                    else:
                        context.bot.answer_callback_query(
                            update.callback_query.id, 
                            "🚫 Access Denied: Account not active.", 
                            show_alert=True
                        )
            return None
        
        kwargs['db_user'] = db_user
        
        # استدعاء الدالة الأصلية
        if inspect.iscoroutinefunction(func):
            async def async_exec():
                return await func(*args, **kwargs)
            return async_exec()
        else:
            return func(*args, **kwargs)
    
    return wrapper

def require_analyst_user(func: Callable) -> Callable:
    """
    ديكوراتور يتحقق مما إذا كان المستخدم لديه دور ANALYST.
    الإصدار المصحح: يتعامل بشكل صحيح مع الدوال المتزامنة وغير المتزامنة.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        db_user = kwargs.get('db_user')
        
        if not db_user or db_user.user_type != UserType.ANALYST:
            # البحث عن update و context في الوسائط
            update = None
            for arg in args:
                if hasattr(arg, 'effective_user'):  # Update object
                    update = arg
                    break
            
            log.warning(f"Blocked analyst-only command for non-analyst user {update.effective_user.id if update else 'unknown'}.")
            message = "🚫 <b>Permission Denied</b>\nThis command is available for analysts only."
            
            if update:
                if update.message:
                    if inspect.iscoroutinefunction(func):
                        async def async_reply():
                            await update.message.reply_html(message)
                        return async_reply()
                    else:
                        update.message.reply_html(message)
                elif update.callback_query:
                    if inspect.iscoroutinefunction(func):
                        async def async_answer():
                            await update.callback_query.answer("🚫 Permission Denied: Analysts only.", show_alert=True)
                        return async_answer()
                    else:
                        # البحث عن context
                        context = None
                        for arg in args:
                            if hasattr(arg, 'bot'):
                                context = arg
                                break
                        if context:
                            context.bot.answer_callback_query(
                                update.callback_query.id,
                                "🚫 Permission Denied: Analysts only.", 
                                show_alert=True
                            )
            return None
            
        # استدعاء الدالة الأصلية
        if inspect.iscoroutinefunction(func):
            async def async_exec():
                return await func(*args, **kwargs)
            return async_exec()
        else:
            return func(*args, **kwargs)
    
    return wrapper

def require_channel_subscription(func: Callable) -> Callable:
    """
    ديكوراتور يفرض الاشتراك في القناة.
    الإصدار المصحح: يتعامل بشكل صحيح مع الدوال غير المتزامنة فقط.
    """
    if not inspect.iscoroutinefunction(func):
        raise TypeError("@require_channel_subscription can only be used with async functions")
    
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        channel_id_str = settings.TELEGRAM_CHAT_ID
        if not channel_id_str:
            return await func(*args, **kwargs)

        # البحث عن update و context في الوسائط
        update = None
        context = None
        
        for arg in args:
            if hasattr(arg, 'effective_user'):  # Update object
                update = arg
            elif hasattr(arg, 'bot'):  # Context object
                context = arg

        user = update.effective_user if update else None
        if not user: 
            return await func(*args, **kwargs)

        try:
            if context and context.bot:
                member = await context.bot.get_chat_member(chat_id=channel_id_str, user_id=user.id)
                if member.status in ['creator', 'administrator', 'member', 'restricted']:
                    return await func(*args, **kwargs)
                else:
                    raise ValueError(f"User is not a member, status: {member.status}")
            else:
                raise RuntimeError("Context or bot not available")
        except Exception as e:
            log.info(f"User {user.id} blocked from command due to not being in channel {channel_id_str}. Error: {e}")
            
            channel_link = settings.TELEGRAM_CHANNEL_INVITE_LINK
            message = "⚠️ <b>Subscription Required</b>\n\nTo use this bot, you must first be a member of our channel."
            
            if update:
                if update.message:
                    await update.message.reply_html(
                        text=message,
                        reply_markup=build_subscription_keyboard(channel_link),
                        disable_web_page_preview=True
                    )
                elif update.callback_query:
                    await update.callback_query.answer("Please subscribe to our main channel first.", show_alert=True)
            return
    
    return async_wrapper