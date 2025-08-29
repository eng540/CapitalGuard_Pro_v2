# --- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from __future__ import annotations
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from .auth import ALLOWED_FILTER
from .keyboards import control_panel_keyboard
from .conversation_handlers import (
    build_newrec_conversation, management_callback_handlers, on_free_text
)

log = logging.getLogger(__name__)

# ——— أوامر عامة ———
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحبًا! استخدم /newrec لإنشاء توصية جديدة، /open لعرض المفتوحة، /list لعرض جميع التوصيات، /analytics للملخص."
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/newrec — إنشاء توصية\n"
        "/open — عرض التوصيات المفتوحة\n"
        "/list — عرض جميع التوصيات\n"
        "/analytics — ملخص الأداء"
    )

async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service):
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return
    for r in items:
        await update.message.reply_html(
            f"<b>#{r.id:04d}</b> — {r.asset.value} ({r.side.value})\n"
            f"Entry: {r.entry.value:g} | SL: {r.stop_loss.value:g}\n"
            f"TPs: " + " , ".join([f"{x:g}" for x in r.targets.values]),
            reply_markup=control_panel_keyboard(r.id, is_open=(r.status.upper()=="OPEN"))
        )

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service):
    items = trade_service.list_all()
    if not items:
        await update.message.reply_text("لا توجد توصيات.")
        return
    for r in items[:30]:
        await update.message.reply_html(
            f"<b>#{r.id:04d}</b> — {r.asset.value} ({r.side.value}) [{r.status}]",
            reply_markup=control_panel_keyboard(r.id, is_open=(r.status.upper()=="OPEN"))
        )

async def cmd_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE, *, analytics_service):
    summary = analytics_service.performance_summary()
    text = "📊 <b>ملخص الأداء</b>\n" + "\n".join([f"• {k}: {v}" for k,v in summary.items()])
    await update.message.reply_html(text)

# ——— التسجيل المركزي ———
def register_all_handlers(app: Application, *, trade_service, analytics_service) -> None:
    # محادثة إنشاء التوصية
    app.add_handler(build_newrec_conversation(trade_service=trade_service))

    # أوامر عامة
    app.add_handler(CommandHandler("start", cmd_start, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("help",  cmd_help,  filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("open",  lambda u,c: cmd_open(u,c,trade_service=trade_service), filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("list",  lambda u,c: cmd_list(u,c,trade_service=trade_service), filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("analytics", lambda u,c: cmd_analytics(u,c,analytics_service=analytics_service), filters=ALLOWED_FILTER))

    # إدارة التوصيات (أزرار داخل البوت)
    for h in management_callback_handlers(trade_service=trade_service):
        app.add_handler(h)

    # رسائل المتابعة (أرقام بعد ضغط الأزرار)
    app.add_handler(MessageHandler(ALLOWED_FILTER & filters.TEXT & ~filters.COMMAND,
                                   lambda u,c: on_free_text(u,c,trade_service=trade_service)))

    log.info("Telegram handlers registered.")
# --- END OF FILE ---