# --- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from __future__ import annotations

from telegram.ext import (
    Application,
    CommandHandler,
)
from telegram.ext import filters
from telegram.constants import ChatType

from .conversation_handlers import build_newrec_conversation
from .management_handlers import (
    # أزرار/إجراءات الإدارة (موجودة مسبقًا)
    register_management_callbacks,
    # أوامر نصية عامة نضيفها هنا
    help_cmd,
    list_open_cmd,
    list_cmd,
    analytics_cmd,
)


def register_all_handlers(app: Application, services_pack: dict) -> None:
    """
    نقطة تجميع واحدة لتسجيل كل Handlers.
    - تحفظ الخدمات في bot_data
    - تسجل محادثة /newrec
    - تسجل الأوامر النصية (/help, /open, /list, /analytics)
    - تسجل الكول باك الخاصة بلوحة الإدارة
    """
    # نحقن الخدمات ليستعملها الـ handlers
    app.bot_data.update(services_pack or {})

    # -- محادثة إنشاء توصية جديدة --
    app.add_handler(build_newrec_conversation())

    # -- أوامر نصية عامة (تعمل في الخاص فقط) --
    private = filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("help", help_cmd, filters=private))
    app.add_handler(CommandHandler("open", list_open_cmd, filters=private))
    # /list [SYMBOL] [STATUS]
    app.add_handler(CommandHandler("list", list_cmd, filters=private))
    app.add_handler(CommandHandler("analytics", analytics_cmd, filters=private))

    # -- أزرار الإدارة (CallbackQuery) --
    register_management_callbacks(app)