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

# محادثة إنشاء التوصية + أزرار النشر/الإلغاء
from .conversation_handlers import (
    get_recommendation_conversation_handler,
    publish_recommendation,
    cancel_publication,
)

# إدارة التوصيات (عرض/إغلاق/تعديل) + دوال إدخال
from .management_handlers import (
    open_cmd,
    list_count_cmd,
    click_close_now,
    received_exit_price,
    confirm_close,
    cancel_close,
    click_amend_sl,
    received_new_sl,
    click_amend_tp,
    received_new_tps,
    show_history,
)

# معالج أخطاء عام
from .errors import register_error_handler

log = logging.getLogger(__name__)

# ======================
# أوامر عامة
# ======================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("START from id=%s username=%s", update.effective_user.id, update.effective_user.username)
    await update.message.reply_html(
        "👋 أهلاً بك في <b>CapitalGuard Bot</b>.\n"
        "استخدم /help للاطلاع على الأوامر."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("HELP from id=%s", update.effective_user.id)
    await update.message.reply_html(
        "<b>الأوامر المتاحة:</b>\n\n"
        "• <code>/newrec</code> — إنشاء توصية بمحادثة موجّهة\n"
        "• <code>/open [SYMBOL]</code> — عرض التوصيات المفتوحة (اختياري فلترة بالرمز)\n"
        "• <code>/list [SYMBOL]</code> — عدّ مفتوحة (اختياري فلترة)\n"
        "• <code>/analytics</code> — ملخص أداء\n"
        "• <code>/ping</code> — اختبار الاتصال"
    )

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
    application.add_handler(CommandHandler("ping", ping_cmd, filters=filters.ALL))  # للسماح بفحص التوصيل
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

    # 4) إدارة التوصيات + الإغلاق/تعديل/السجل
    application.add_handler(CallbackQueryHandler(click_close_now,   pattern=r"^rec:close:\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_close,     pattern=r"^rec:confirm_close:\d+:[0-9.]+$"))
    application.add_handler(CallbackQueryHandler(cancel_close,      pattern=r"^rec:cancel_close:\d+$"))

    application.add_handler(CallbackQueryHandler(click_amend_sl,    pattern=r"^rec:amend_sl:\d+$"))
    application.add_handler(CallbackQueryHandler(click_amend_tp,    pattern=r"^rec:amend_tp:\d+$"))
    application.add_handler(CallbackQueryHandler(show_history,      pattern=r"^rec:history:\d+$"))

    # استقبال مدخلات المستخدم في DM — Group أعلى من المحادثة لتجنّب التعارض
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_new_sl), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_new_tps), group=1)

    # 5) معالج الأخطاء العام
    register_error_handler(application)