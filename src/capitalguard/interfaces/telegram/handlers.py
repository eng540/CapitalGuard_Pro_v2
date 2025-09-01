--- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from __future__ import annotations
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from .auth import ALLOWED_FILTER
from .keyboards import control_panel_keyboard
from .conversation_handlers import build_newrec_conversation
from .management_handlers import (
    build_management_callbacks, build_management_text_receivers, build_management_commands
)

log = logging.getLogger(__name__)

def register_all_handlers(app: Application, services: dict):
    # حقن الخدمات في التطبيق
    app.bot_data.setdefault("services", services)

    # 1) محادثة إنشاء التوصية
    app.add_handler(build_newrec_conversation(), group=0)

    # 2) أزرار الإدارة (CallbackQueryHandlers)
    for cb in build_management_callbacks():
        app.add_handler(cb, group=1)

    # 3) استقبال النصوص اللاحقة لطلبات SL/TP/Close
    for mh in build_management_text_receivers():
        app.add_handler(mh, group=2)

    # 4) أوامر القوائم/التحليلات
    for ch in build_management_commands():
        app.add_handler(ch, group=3)

    # 5) أوامر مساعدة أساسية
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE & ALLOWED_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=filters.ChatType.PRIVATE & ALLOWED_FILTER))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا بك في CapitalGuard Pro 👋\n"
        "الأوامر:\n"
        "• /newrec — إنشاء توصية\n"
        "• /open — قائمة المفتوحة\n"
        "• /list — تصفية عامة (رمز/حالة)\n"
        "• /analytics — ملخص الأداء\n"
        "ملاحظة: هذه الأوامر تعمل في الخاص فقط للمستخدمين المصرّح لهم."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("استخدم /newrec للبدء. سيتم التدقيق قبل النشر، والقناة للعرض فقط بلا أزرار.")
--- END OF FILE ---