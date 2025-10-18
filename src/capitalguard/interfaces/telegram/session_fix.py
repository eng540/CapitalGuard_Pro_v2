# src/capitalguard/interfaces/telegram/session_fix.py
"""
إصلاح جلسات المستخدم ومنع انتهاء المهلة - الإصدار النهائي مع إصلاحات الـ Async
"""

import logging
import time
import inspect
from functools import wraps
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

async def reset_user_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إعادة تعيين جلسة المستخدم بشكل آمن"""
    try:
        user_id = update.effective_user.id
        
        # تنظيف جميع حالات المحادثة والمهلات
        conversation_keys = [
            'rec_creation_draft', 
            'channel_picker_selection',
            'last_activity',
            'db_user_id',
            'conversation_started',
            'selected_channels',
            'creation_method',
            'current_page',
            'asset_search_term',
            'last_price_check'
        ]
        
        for key in conversation_keys:
            if key in context.user_data:
                del context.user_data[key]
                
        # إعادة تعيين وقت النشاط
        context.user_data['last_activity'] = time.time()
        context.user_data['session_reset'] = True
        context.user_data['session_start'] = time.time()
            
        log.info(f"Session reset for user {user_id}")
        return True
        
    except Exception as e:
        log.error(f"Error resetting session: {e}")
        return False

async def update_session_activity(context: ContextTypes.DEFAULT_TYPE):
    """تحديث وقت النشاط الأخير للجلسة"""
    try:
        context.user_data['last_activity'] = time.time()
        return True
    except Exception:
        return False

def is_session_active(context: ContextTypes.DEFAULT_TYPE, timeout_minutes=120):
    """التحقق إذا كانت الجلسة لا تزال نشطة"""
    try:
        last_activity = context.user_data.get('last_activity', 0)
        current_time = time.time()
        time_diff = current_time - last_activity
        return time_diff < (timeout_minutes * 60)
    except Exception:
        return False

def safe_command_handler(handler_func: Callable) -> Callable:
    """
    غلاف آمن للأوامر يمنع انتهاء الجلسة.
    الإصدار المصحح: يتعامل بشكل صحيح مع الدوال غير المتزامنة.
    """
    if not inspect.iscoroutinefunction(handler_func):
        raise TypeError("@safe_command_handler can only be used with async functions")
    
    @wraps(handler_func)
    async def async_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            # تحديث النشاط أولاً
            await update_session_activity(context)
            
            # تنظيف أي حالات محادثة قديمة
            if 'conversation_started' in context.user_data:
                del context.user_data['conversation_started']
                
            # تنفيذ الأمر الأصلي
            return await handler_func(update, context, *args, **kwargs)
        except Exception as e:
            log.error(f"Error in safe_command_handler: {e}")
            if update.message:
                await update.message.reply_text("❌ حدث خطأ في النظام. يرجى المحاولة مرة أخرى.")
            return None
    
    return async_wrapper

def safe_conversation_handler(handler_func: Callable) -> Callable:
    """
    غلاف آمن لمعالجات المحادثات.
    الإصدار المصحح: يتعامل بشكل صحيح مع الدوال غير المتزامنة.
    """
    if not inspect.iscoroutinefunction(handler_func):
        raise TypeError("@safe_conversation_handler can only be used with async functions")
    
    @wraps(handler_func)
    async def async_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            # تحديث النشاط
            await update_session_activity(context)
            return await handler_func(update, context, *args, **kwargs)
        except Exception as e:
            log.error(f"Error in safe_conversation_handler: {e}")
            return await handler_func(update, context, *args, **kwargs)
            
    return async_wrapper

async def check_and_reset_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من حالة الجلسة وإعادة تعيينها إذا انتهت"""
    try:
        if not is_session_active(context):
            await reset_user_session(update, context)
            return False
        return True
    except Exception as e:
        log.error(f"Error checking session: {e}")
        await reset_user_session(update, context)
        return False

def get_session_duration(context: ContextTypes.DEFAULT_TYPE):
    """الحصول على مدة الجلسة الحالية بالثواني"""
    try:
        session_start = context.user_data.get('session_start', time.time())
        return time.time() - session_start
    except Exception:
        return 0