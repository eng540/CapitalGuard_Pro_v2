# --- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from __future__ import annotations
from telegram.ext import Application

from .conversation_handlers import register_newrec_conversation
from .management_handlers import register_management_handlers

def register_all_handlers(application: Application, services_pack: dict) -> None:
    """
    يُسجّل جميع Handlers.
    ملاحظة: لا نضيف أزرارًا في القناة — كل الإدارة تتم في الخاص.
    """
    # حفظ الخدمات في bot_data
    application.bot_data.update(services_pack)

    # محادثة إنشاء توصية
    register_newrec_conversation(application)

    # إدارة (SL/TP/Close/History) عبر لوحة التحكم الخاصة + الرسائل اللاحقة
    register_management_handlers(application)
# --- END OF FILE ---