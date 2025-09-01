#--- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler
from .helpers import get_service
from .keyboards import recommendation_management_keyboard
from .auth import ALLOWED_FILTER

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
    analytics_service = get_service(context, "analytics_service")
    summary = analytics_service.performance_summary()
    text = "📊 <b>ملخص الأداء</b>\n" + "\n".join([f"• {k.replace('_', ' ').title()}: {v}" for k, v in summary.items()])
    await update.message.reply_html(text)

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service = get_service(context, "trade_service")
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return
    for it in items:
        text = (f"<b>#{it.id}</b> — <b>{it.asset.value}</b> ({it.side.value})")
        await update.message.reply_html(text, reply_markup=recommendation_management_keyboard(it.id))

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("analytics", analytics_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_FILTER))
#--- END OF FILE ---