#- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from .auth import ALLOWED_FILTER
from .conversation_handlers import get_recommendation_conversation_handler
from .management_handlers import register_management_handlers
from .commands import start_cmd, help_cmd, analytics_cmd

def register_all_handlers(application: Application):
    """
    الدالة المركزية والوحيدة لتسجيل جميع معالجات البوت.
    تعتمد على أن الخدمات محقونة مسبقًا في application.bot_data["services"].
    """
    # 1. الأوامر الأساسية
    application.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("analytics", analytics_cmd, filters=ALLOWED_FILTER))
    
    # 2. محادثة إنشاء التوصية
    application.add_handler(get_recommendation_conversation_handler())

    # 3. معالجات إدارة التوصيات المفتوحة
    register_management_handlers(application)
#--- END OF FILE ---