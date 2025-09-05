# --- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
import io
import csv
from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ContextTypes, CommandHandler, filters

from .helpers import get_service
# ✅ MODIFIED: Import the new DB-backed filter.
from .auth import ALLOWED_USER_FILTER
from .ui_texts import build_analyst_stats_text
from .keyboards import build_open_recs_keyboard
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
# ✅ NEW: Import the UserRepository for registration.
from capitalguard.infrastructure.db.user_repository import UserRepository

# --- Conversation Handler related constants ---
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(I_ASSET_CHOICE, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(3, 9)
USER_PREFERENCE_KEY = "preferred_creation_method"


# ✅ NEW: Handler for the /register command.
async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows a new user to register themselves with the system."""
    user_repo = UserRepository()
    telegram_id = update.effective_user.id
    
    user = user_repo.find_by_telegram_id(telegram_id)
    if user:
        await update.message.reply_html("👋 أهلاً بعودتك! أنت مسجل بالفعل في النظام.")
    else:
        user_repo.register_user(telegram_id=telegram_id, user_type='analyst')
        await update.message.reply_html("✅ <b>تم تسجيلك بنجاح!</b>\n\nأهلاً بك في CapitalGuard. يمكنك الآن البدء باستخدام الأوامر مثل /newrec.")


def main_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def change_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تغيير طريقة الإدخال", callback_data="change_method")]])

async def newrec_entry_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (function content remains the same)
    preferred_method = context.user_data.get(USER_PREFERENCE_KEY)
    if preferred_method == "interactive":
        await update.message.reply_text(
            "🚀 سنبدأ المُنشئ التفاعلي.\n(اختر الأصل من الأزرار أو اكتب الرمز مباشرة)",
            reply_markup=change_method_keyboard()
        )
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
    await update.message.reply_html("👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /register للتسجيل أو /help للمساعدة.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "• <code>/register</code> — سجل نفسك كمستخدم جديد.\n"
        "• <code>/newrec</code> — إنشاء توصية جديدة (للمستخدمين المسجلين).\n"
        "• <code>/open [filter]</code> — عرض لوحة القيادة (للمستخدمين المسجلين).\n"
        "• <code>/stats</code> — ملخّص الأداء (للمستخدمين المسجلين).\n"
        "• <code>/export</code> — تصدير التوصيات (للمستخدمين المسجلين).\n"
        "• <code>/settings</code> — إدارة التفضيلات (للمستخدمين المسجلين)."
    )

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (function content remains the same)
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    filters = {}
    filter_text_parts = []
    if context.args:
        for arg in context.args:
            arg_lower = arg.lower()
            if arg_lower in ["long", "short"]:
                filters["side"] = arg_lower
                filter_text_parts.append(f"الاتجاه: {arg_lower.upper()}")
            elif arg_lower in ["pending", "active"]:
                filters["status"] = arg_lower
                filter_text_parts.append(f"الحالة: {arg_lower.upper()}")
            else:
                filters["symbol"] = arg_lower
                filter_text_parts.append(f"الرمز: {arg_lower.upper()}")
    context.user_data['last_open_filters'] = filters
    items = trade_service.list_open(**filters)
    if not items:
        await update.message.reply_text("✅ لا توجد توصيات مفتوحة تطابق الفلتر الحالي.")
        return
    keyboard = build_open_recs_keyboard(items, current_page=1, price_service=price_service)
    header_text = "<b>📊 لوحة قيادة التوصيات المفتوحة</b>"
    if filter_text_parts:
        header_text += f"\n<i>فلترة حسب: {', '.join(filter_text_parts)}</i>"
    await update.message.reply_html(f"{header_text}\nاختر توصية لعرض لوحة التحكم الخاصة بها:", reply_markup=keyboard)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (function content remains the same)
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    stats = analytics_service.performance_summary()
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (function content remains the same)
    await update.message.reply_text("جاري تجهيز ملف التصدير...")
    trade_service: TradeService = get_service(context, "trade_service")
    all_recs = trade_service.list_all()
    if not all_recs:
        await update.message.reply_text("لا توجد بيانات للتصدير.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    header = ["id","asset","side","status","market","entry_price","stop_loss","targets","exit_price","notes","created_at","closed_at"]
    writer.writerow(header)
    for rec in all_recs:
        row = [rec.id, rec.asset.value, rec.side.value, rec.status.value, rec.market, rec.entry.value, rec.stop_loss.value, ", ".join(map(str, rec.targets.values)), rec.exit_price, rec.notes, rec.created_at.strftime('%Y-%m-%d %H:%M:%S') if rec.created_at else "", rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""]
        writer.writerow(row)
    output.seek(0)
    bytes_buffer = io.BytesIO(output.getvalue().encode('utf-8'))
    csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    await update.message.reply_document(document=csv_file, caption="تم إنشاء التصدير.")


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("⚙️ الإعدادات\n\nاختر طريقتك المفضلة للوضع الافتراضي لأمر /newrec:", reply_markup=main_creation_keyboard())
    return CHOOSE_METHOD

def register_commands(app: Application):
    # ✅ MODIFIED: Public commands that everyone can use.
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("register", register_command)) # Public registration

    # ✅ MODIFIED: Protected commands that now use the database filter.
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_USER_FILTER))
# --- END OF FILE ---