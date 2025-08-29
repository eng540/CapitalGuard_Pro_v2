# --- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from __future__ import annotations
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
from .conversation_handlers import get_recommendation_conversation_handler, cmd_publish, cmd_cancel
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
)
from .errors import register_error_handler
from .ui_texts import WELCOME, HELP

log = logging.getLogger(__name__)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(WELCOME)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(HELP)

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong ✅")

async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, analytics_service):
    summary = analytics_service.performance_summary()
    text = "📊 <b>ملخص الأداء</b>\n" + "\n".join([f"• {k.replace('_',' ').title()}: {v}" for k, v in summary.items()])
    await update.message.reply_html(text)

def register_all_handlers(
    application: Application,
    *,
    trade_service,
    analytics_service,
) -> None:
    """
    يسجّل جميع Handlers. كل التفاعل داخل البوت فقط (لا تعامل في القناة).
    """
    # أوامر عامة
    application.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER & filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("help", help_cmd,   filters=ALLOWED_FILTER & filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("ping", ping_cmd,   filters=filters.ALL))

    application.add_handler(
        CommandHandler(
            "analytics",
            partial(analytics_cmd, analytics_service=analytics_service),
            filters=ALLOWED_FILTER & filters.ChatType.PRIVATE,
        )
    )

    # إدارة
    application.add_handler(
        CommandHandler("open", partial(open_cmd, trade_service=trade_service), filters=ALLOWED_FILTER & filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("list", partial(list_count_cmd, trade_service=trade_service), filters=ALLOWED_FILTER & filters.ChatType.PRIVATE)
    )

    # محادثة /newrec + نشر/إلغاء
    application.add_handler(get_recommendation_conversation_handler(ALLOWED_FILTER))
    application.add_handler(CommandHandler("publish", cmd_publish, filters=ALLOWED_FILTER & filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("cancel",  cmd_cancel,  filters=ALLOWED_FILTER & filters.ChatType.PRIVATE))

    # أزرار إدارة التوصية
    application.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_close,  pattern=r"^rec:confirm_close:\d+:[0-9.]+$"))
    application.add_handler(CallbackQueryHandler(cancel_close,   pattern=r"^rec:cancel_close:\d+$"))

    application.add_handler(CallbackQueryHandler(click_amend_sl, pattern=r"^rec:amend_sl:\d+$"))
    application.add_handler(CallbackQueryHandler(click_amend_tp, pattern=r"^rec:amend_tp:\d+$"))

    # MessageHandlers لإدخال قيم SL/TP/Exit Price (ترتيب Group سابق لمحادثة الإنشاء)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_new_sl),     group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_new_tps),    group=1)

    # لوج تشخيصي أخير
    async def _log_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message and update.message.text:
            log.info("TEXT '%s' from id=%s", update.message.text.strip(), update.effective_user.id)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _log_text), group=99)

    register_error_handler(application)
# --- END OF FILE ---