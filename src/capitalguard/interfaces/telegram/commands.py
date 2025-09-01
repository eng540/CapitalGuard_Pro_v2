#--- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
from telegram import Update
from telegram.ext import ContextTypes

# --- دالة مساعدة للوصول إلى الخدمات ---
def get_service(context: ContextTypes.DEFAULT_TYPE, service_name: str):
    return context.application.bot_data["services"][service_name]

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
    try:
        analytics_service = get_service(context, "analytics_service")
        summary = analytics_service.performance_summary()
        text = "📊 <b>ملخص الأداء</b>\n" + "\n".join([f"• {k.replace('_', ' ').title()}: {v}" for k, v in summary.items()])
        await update.message.reply_html(text)
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ: {e}")
#--- END OF FILE ---```