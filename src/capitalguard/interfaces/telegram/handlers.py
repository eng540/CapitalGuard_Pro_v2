#--- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
)
from capitalguard.config import settings

# استيراد جميع المعالجات الفرعية
from .conversation_handlers import get_recommendation_conversation_handler, publish_recommendation, cancel_publication
from .management_handlers import open_cmd, click_close_now, received_exit_price, confirm_close, cancel_close

# --- فلتر المستخدمين المصرح لهم (المكان المركزي) ---
ALLOWED_USERS = {int(uid.strip()) for uid in (settings.TELEGRAM_ALLOWED_USERS or "").split(",") if uid.strip()}
ALLOWED_FILTER = filters.User(list(ALLOWED_USERS)) if ALLOWED_USERS else filters.ALL

# --- الأوامر البسيطة ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /help للمساعدة.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (محتوى الدالة يبقى كما هو)
    pass

async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (محتوى الدالة يبقى كما هو)
    pass

def register_all_handlers(application: Application):
    """
    الدالة المركزية لتسجيل جميع معالجات البوت.
    """
    # 1. الأوامر الأساسية (محمية بفلتر)
    application.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("analytics", analytics_cmd, filters=ALLOWED_FILTER))
    
    # 2. محادثة إنشاء التوصية (مع تمرير الفلتر)
    application.add_handler(get_recommendation_conversation_handler(ALLOWED_FILTER)) # ✅ تم تمرير الفلتر هنا
    application.add_handler(CallbackQueryHandler(publish_recommendation, pattern=r"^rec:publish:"))
    application.add_handler(CallbackQueryHandler(cancel_publication, pattern=r"^rec:cancel:"))

    # 3. معالجات إدارة التوصيات المفتوحة
    application.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:"))
    application.add_handler(CallbackQueryHandler(confirm_close, pattern=r"^rec:confirm_close:"))
    application.add_handler(CallbackQueryHandler(cancel_close, pattern=r"^rec:cancel_close:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price), group=1)
#--- END OF FILE ---