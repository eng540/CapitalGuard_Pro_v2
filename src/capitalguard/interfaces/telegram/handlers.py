# --- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from functools import partial
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .auth import ALLOWED_FILTER
from .conversation_handlers import (
    get_recommendation_conversation_handler,
    publish_recommendation,
    cancel_publication,
)
from .management_handlers import (
    open_cmd,
    list_count_cmd,
    click_close_now,
    received_exit_price,
    confirm_close,
    cancel_close,
)
from .errors import register_error_handler
from .ui_texts import WELCOME, HELP

log = logging.getLogger(__name__)

# ======================
# أوامر عامة
# ======================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("START from id=%s username=%s", update.effective_user.id, update.effective_user.username)
    await update.message.reply_html(WELCOME)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("HELP from id=%s", update.effective_user.id)
    await update.message.reply_html(HELP)

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("PING from id=%s", update.effective_user.id)
    await update.message.reply_text("pong ✅")

async def analytics_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    analytics_service,
):
    log.info("ANALYTICS from id=%s", update.effective_user.id)
    summary = analytics_service.performance_summary()
    text = "📊 <b>ملخص الأداء</b>\n" + "\n".join(
        [f"• {k.replace('_',' ').title()}: {v}" for k, v in summary.items()]
    )
    await update.message.reply_html(text)

# ======================
# التسجيل المركزي
# ======================
def register_all_handlers(application: Application, services: dict):
    """
    يسجل جميع Handlers على نفس Application.
    - الأوامر: حقن صريح عبر partial لتمرير الخدمات المطلوبة.
    - المحادثات والـ CallbackQueries: تستخدم bot_data أو خدمات الحقن حسب الحاجة.
    """
    # 1) أوامر عامة
    application.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("ping", ping_cmd, filters=filters.ALL))  # فحص توصيل
    application.add_handler(
        CommandHandler(
            "analytics",
            partial(analytics_cmd, analytics_service=services["analytics_service"]),
            filters=ALLOWED_FILTER,
        )
    )

    # 2) أوامر الإدارة (قائمة المفتوحة + عدّ سريع)
    application.add_handler(
        CommandHandler(
            "open",
            partial(open_cmd, trade_service=services["trade_service"]),
            filters=ALLOWED_FILTER,
        )
    )
    application.add_handler(
        CommandHandler(
            "list",
            partial(list_count_cmd, trade_service=services["trade_service"]),
            filters=ALLOWED_FILTER,
        )
    )

    # 3) محادثة إنشاء التوصية + أزرار نشر/إلغاء
    application.add_handler(get_recommendation_conversation_handler(ALLOWED_FILTER))
    application.add_handler(CallbackQueryHandler(publish_recommendation, pattern=r"^rec:publish:"))
    application.add_handler(CallbackQueryHandler(cancel_publication, pattern=r"^rec:cancel:"))

    # 4) إدارة التوصيات + الإغلاق (قناة→DM)
    application.add_handler(CallbackQueryHandler(click_close_now,   pattern=r"^rec:close:\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_close,     pattern=r"^rec:confirm_close:\d+:[0-9.]+$"))
    application.add_handler(CallbackQueryHandler(cancel_close,      pattern=r"^rec:cancel_close:\d+$"))

    # 5) استقبال سعر الخروج في DM (Group أعلى من المحادثة لتجنّب التعارض)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price), group=1)

    # 6) لوج لكل نص يصل (تشخيص فقط)
    async def _log_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        log.info("TEXT '%s' from id=%s", (update.message.text or "").strip(), update.effective_user.id)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _log_text), group=99)

    # 7) معالج الأخطاء العام
    register_error_handler(application)
# --- END OF FILE ---