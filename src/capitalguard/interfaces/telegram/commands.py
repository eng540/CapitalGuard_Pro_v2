# --- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
import io
import csv
from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ContextTypes, CommandHandler
from .helpers import get_service
from .auth import ALLOWED_FILTER
from .ui_texts import build_analyst_stats_text
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService

# يجب أن تتطابق مع conversation_handlers.py
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(I_ASSET_CHOICE, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(3, 9)
USER_PREFERENCE_KEY = "preferred_creation_method"

def main_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def change_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تغيير طريقة الإدخال", callback_data="change_method")]])

async def newrec_entry_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    نقطة الدخول الذكية. تعرض لوحة الاختيار أو تنقل المستخدم مباشرة
    للحالة المناسبة. (بدون أي استدعاءات لروبوتات أخرى لتجنب الارتباط الدائري)
    """
    preferred_method = context.user_data.get(USER_PREFERENCE_KEY)
    if preferred_method == "interactive":
        # سيتولى ConversationHandler تحويل التدفق إلى start_interactive_builder
        await update.message.reply_text(
            "🚀 سنبدأ المُنشئ التفاعلي.\n(اختر الأصل من الأزرار أو اكتب الرمز مباشرة)",
            reply_markup=change_method_keyboard()
        )
        # نعيد حالة البداية لكي يلتقطها conversation_handlers.start_interactive_builder
        return CHOOSE_METHOD

    if preferred_method == "quick":
        await update.message.reply_text(
            "⚡️ وضع الأمر السريع.\n\n"
            "أرسل توصيتك برسالة واحدة تبدأ بـ /rec\n"
            "مثال: /rec BTCUSDT LONG 65000 64000 66k",
            reply_markup=change_method_keyboard()
        )
        return QUICK_COMMAND

    if preferred_method == "editor":
        await update.message.reply_text(
            "📋 وضع المحرّر النصي.\n\n"
            "ألصق توصيتك بشكل حقول:\n"
            "Asset: BTCUSDT\nSide: LONG\nEntry: 65000\nStop: 64000\nTargets: 66k 68k",
            reply_markup=change_method_keyboard()
        )
        return TEXT_EDITOR

    await update.message.reply_text(
        "🚀 إنشاء توصية جديدة.\n\nاختر طريقتك المفضلة للإدخال:",
        reply_markup=main_creation_keyboard()
    )
    return CHOOSE_METHOD

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /help للمساعدة.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "• <code>/newrec</code> — إنشاء توصية جديدة.\n"
        "• <code>/open</code> — عرض التوصيات المفتوحة.\n"
        "• <code>/stats</code> — ملخّص الأداء.\n"
        "• <code>/export</code> — تصدير التوصيات.\n"
        "• <code>/settings</code> — إدارة التفضيلات."
    )

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service: TradeService = get_service(context, "trade_service")
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة حالياً.")
        return
    lines = ["<b>التوصيات المفتوحة:</b>"]
    for it in items:
        lines.append(f"• #{it.id} — {it.asset.value} ({it.side.value})")
    await update.message.reply_html("\n".join(lines))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    stats = analytics_service.performance_summary()
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("جاري تجهيز ملف التصدير...")
    trade_service: TradeService = get_service(context, "trade_service")
    all_recs = trade_service.list_all()
    if not all_recs:
        await update.message.reply_text("لا توجد بيانات للتصدير.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        "id","asset","side","status","market","entry_price","stop_loss",
        "targets","exit_price","notes","created_at","closed_at"
    ]
    writer.writerow(header)
    for rec in all_recs:
        row = [
            rec.id, rec.asset.value, rec.side.value, rec.status.value, rec.market,
            rec.entry.value, rec.stop_loss.value, ", ".join(map(str, rec.targets.values)),
            rec.exit_price, rec.notes,
            rec.created_at.strftime('%Y-%m-%d %H:%M:%S') if rec.created_at else "",
            rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""
        ]
        writer.writerow(row)

    output.seek(0)
    bytes_buffer = io.BytesIO(output.getvalue().encode('utf-8'))
    csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    await update.message.reply_document(document=csv_file, caption="تم إنشاء التصدير.")

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "⚙️ الإعدادات\n\n"
        "اختر طريقتك المفضلة للوضع الافتراضي لأمر /newrec:",
        reply_markup=main_creation_keyboard()
    )
    return CHOOSE_METHOD

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_FILTER))
# --- END OF FILE ---