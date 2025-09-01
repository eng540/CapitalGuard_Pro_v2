#--- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
)
from capitalguard.config import settings
from .conversation_handlers import get_recommendation_conversation_handler, publish_recommendation, cancel_publication
from .management_handlers import register_management_handlers, open_cmd

ALLOWED_USERS = {int(uid.strip()) for uid in (settings.TELEGRAM_ALLOWED_USERS or "").split(",") if uid.strip()}
ALLOWED_FILTER = filters.User(list(ALLOWED_USERS)) if ALLOWED_USERS else filters.ALL

# --- الأوامر البسيطة ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /help للمساعدة.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>الأوامر المتاحة:</b>\n\n"
        "• <code>/newrec</code> — بدء محادثة لإنشاء توصية.\n"
        "• <code>/open</code> — عرض وإدارة التوصيات المفتوحة.\n"
        "• <code>/analytics</code> — عرض ملخص الأداء."
    )

async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    analytics_service = context.application.bot_data["services"]["analytics_service"]
    summary = analytics_service.performance_summary()
    text = "📊 <b>ملخص الأداء</b>\n" + "\n".join([f"• {k.replace('_', ' ').title()}: {v}" for k, v in summary.items()])
    await update.message.reply_html(text)

def register_all_handlers(application: Application):
    """
    الدالة المركزية لتسجيل جميع معالجات البوت.
    """
    # 1. الأوامر الأساسية
    application.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("analytics", analytics_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_FILTER))

    # 2. محادثة إنشاء التوصية وأزرارها
    application.add_handler(get_recommendation_conversation_handler(ALLOWED_FILTER))
    application.add_handler(CallbackQueryHandler(publish_recommendation, pattern=r"^rec:publish:"))
    application.add_handler(CallbackQueryHandler(cancel_publication, pattern=r"^rec:cancel:"))

    # 3. معالجات إدارة التوصيات المفتوحة
    register_management_handlers(application)
#--- END OF FILE ---