# src/capitalguard/interfaces/telegram/session_fix.py
"""
إصلاح جلسات المستخدم ومنع انتهاء المهلة - الإصدار النهائي الكامل
"""

import logging
import time
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
        log.error(f"Error resetting session for user {update.effective_user.id}: {e}")
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

async def safe_command_handler(handler_func):
    """غلاف آمن للأوامر يمنع انتهاء الجلسة"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
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
            return
            
    return wrapper

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

async def safe_conversation_handler(handler_func):
    """غلاف آمن لمعالجات المحادثات"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            # التحقق من الجلسة وتحديث النشاط
            session_ok = await check_and_reset_session(update, context)
            if not session_ok:
                if update.callback_query:
                    await update.callback_query.answer("تم إعادة تعيين الجلسة بسبب عدم النشاط", show_alert=True)
                return await handler_func(update, context, *args, **kwargs)
                
            await update_session_activity(context)
            return await handler_func(update, context, *args, **kwargs)
        except Exception as e:
            log.error(f"Error in safe_conversation_handler: {e}")
            return await handler_func(update, context, *args, **kwargs)
            
    return wrapper

def cleanup_expired_sessions(context: ContextTypes.DEFAULT_TYPE, timeout_minutes=120):
    """تنظيف الجلسات المنتهية (للاستخدام في المهام الدورية)"""
    try:
        cleaned_count = 0
        # هذه دالة مساعدة يمكن استخدامها في مهام تنظيف دورية
        # في نظام حقيقي، قد تحتاج لتكرار على جميع جلسات المستخدمين
        if not is_session_active(context, timeout_minutes):
            # تنظيف جلسة المستخدم الحالي
            keys_to_remove = [
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
            for key in keys_to_remove:
                if key in context.user_data:
                    del context.user_data[key]
                    cleaned_count += 1
        return cleaned_count
    except Exception as e:
        log.error(f"Error cleaning expired sessions: {e}")
        return 0