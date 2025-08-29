# --- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from __future__ import annotations
from telegram.ext import Application, CommandHandler

from .conversation_handlers import build_newrec_conversation, register_panel_handlers

def register_all_handlers(application: Application, services_pack: dict) -> None:
    """
    يربط جميع الأوامر والمحادثات. يعتمد على bot_data لتمرير الخدمات.
    """
    # محادثة إنشاء توصية
    application.add_handler(build_newrec_conversation())

    # أوامر بسيطة مكانها هنا إن أحببت:
    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("مرحبًا! استخدم /newrec لإنشاء توصية.")))
    application.add_handler(CommandHandler("help",  lambda u, c: u.message.reply_text("الأوامر: /newrec  ثم /publish أو /cancel.\nلوحة التحكم تظهر بعد النشر.")))

    # أزرار اللوحة
    register_panel_handlers(application)
# --- END OF FILE ---