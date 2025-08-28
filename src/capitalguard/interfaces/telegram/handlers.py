# src/capitalguard/interfaces/telegram/handlers.py
from functools import partial
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters
from capitalguard.config import settings

# فلاتر الصلاحيات
ALLOWED_USERS = {
    int(uid.strip()) for uid in (settings.TELEGRAM_ALLOWED_USERS or "").split(",") if uid.strip()
}
ALLOWED_FILTER = filters.User(list(ALLOWED_USERS)) if ALLOWED_USERS else filters.ALL

# === أوامر تعتمد حقن صريح عبر partial ===

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /help للمساعدة."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>الأوامر المتاحة:</b>\n\n"
        "• <code>/newrec</code> — بدء محادثة لإنشاء توصية.\n"
        "• <code>/open</code> — عرض وإدارة التوصيات المفتوحة.\n"
        "• <code>/analytics</code> — عرض ملخص الأداء."
    )

async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, analytics_service):
    summary = analytics_service.summarize()  # أو performance_summary() حسب خدمتك
    text = "📊 <b>ملخص الأداء</b>\n" + "\n".join(
        f"• {k.replace('_',' ').title()}: {v}" for k, v in summary.items()
    )
    await update.message.reply_html(text)

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service):
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة حاليًا.")
        return
    # اعرض قائمة مختصرة (يمكنك استعمال قوالبك الحالية)
    lines = [f"#{i.id} {i.asset} — {i.side} @ {i.entry_price}" for i in items]
    await update.message.reply_text("🔓 التوصيات المفتوحة:\n" + "\n".join(lines))

def register_basic_handlers(app: Application, services: dict):
    # بناء Handlers مع حقن صريح للخدمات المطلوبة
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))

    app.add_handler(CommandHandler(
        "analytics",
        partial(analytics_cmd, analytics_service=services["analytics_service"]),
        filters=ALLOWED_FILTER,
    ))

    app.add_handler(CommandHandler(
        "open",
        partial(open_cmd, trade_service=services["trade_service"]),
        filters=ALLOWED_FILTER,
    ))