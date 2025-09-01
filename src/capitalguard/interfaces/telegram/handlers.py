#--- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from telegram.ext import Application, MessageHandler, filters
from .commands import register_commands
from .callbacks import register_callbacks
from .conversation_handlers import get_recommendation_conversation_handler
from .management_handlers import received_exit_price # Note: management_handlers might be deprecated

def register_all_handlers(application: Application):
    """
    الدالة المركزية والوحيدة لتسجيل جميع معالجات البوت.
    """
    from .auth import ALLOWED_FILTER
    
    # 1. تسجيل الأوامر
    register_commands(application)

    # 2. تسجيل استجابات الأزرار
    register_callbacks(application)
    
    # 3. تسجيل المحادثة
    application.add_handler(get_recommendation_conversation_handler(ALLOWED_FILTER))

    # 4. تسجيل معالج الرسائل العامة (لأسعار الإغلاق)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price), group=1)
#--- END OF FILE ---