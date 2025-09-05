# --- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
import io
import csv
from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ContextTypes, CommandHandler

from .helpers import get_service
from .auth import ALLOWED_USER_FILTER
from .ui_texts import build_analyst_stats_text

# ✅ CORRECTED: Keyboards are now imported from the central keyboards.py file.
from .keyboards import build_open_recs_keyboard

from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.user_repository import UserRepository

# --- Conversation Handler constants are still needed here for registration ---
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(I_ASSET_CHOICE, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(3, 9)
USER_PREFERENCE_KEY = "preferred_creation_method"


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_repo = UserRepository()
    telegram_id = update.effective_user.id
    user = user_repo.find_by_telegram_id(telegram_id)

    if user:
        await update.message.reply_html(f"👋 أهلاً بعودتك يا {update.effective_user.first_name}!\n\nاستخدم /help لعرض الأوامر المتاحة.")
    else:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ الموافقة على الشروط وبدء المتابعة", callback_data="user_register_confirm")]])
        welcome_text = "👋 <b>أهلاً بك في CapitalGuard!</b>..."
        await update.message.reply_html(welcome_text, reply_markup=keyboard)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>الأوامر المتاحة:</b>\n\n"
        "• <code>/newrec</code> — إنشاء توصية جديدة.\n"
        # ... (rest of help text)
    )

# ✅ REMOVED: main_creation_keyboard() and change_method_keyboard() are now in keyboards.py

# ... (open_cmd, stats_cmd, export_cmd functions remain unchanged) ...
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service: TradeService = get_service(context, "trade_service"); price_service: PriceService = get_service(context, "price_service"); filters = {}; filter_text_parts = []
    if context.args:
        for arg in context.args:
            arg_lower = arg.lower()
            if arg_lower in ["long", "short"]: filters["side"] = arg_lower; filter_text_parts.append(f"الاتجاه: {arg_lower.upper()}")
            elif arg_lower in ["pending", "active"]: filters["status"] = arg_lower; filter_text_parts.append(f"الحالة: {arg_lower.upper()}")
            else: filters["symbol"] = arg_lower; filter_text_parts.append(f"الرمز: {arg_lower.upper()}")
    context.user_data['last_open_filters'] = filters; items = trade_service.list_open(**filters)
    if not items: await update.message.reply_text("✅ لا توجد توصيات مفتوحة تطابق الفلتر الحالي."); return
    keyboard = build_open_recs_keyboard(items, current_page=1, price_service=price_service)
    header_text = "<b>📊 لوحة قيادة التوصيات المفتوحة</b>"
    if filter_text_parts: header_text += f"\n<i>فلترة حسب: {', '.join(filter_text_parts)}</i>"
    await update.message.reply_html(f"{header_text}\nاختر توصية...", reply_markup=keyboard)
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    analytics_service: AnalyticsService = get_service(context, "analytics_service"); stats = analytics_service.performance_summary(); text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("جاري تجهيز ملف التصدير..."); trade_service: TradeService = get_service(context, "trade_service"); all_recs = trade_service.list_all()
    if not all_recs: await update.message.reply_text("لا توجد بيانات للتصدير."); return
    output = io.StringIO(); writer = csv.writer(output); header = ["id","asset","side","status","market","entry_price","stop_loss","targets","exit_price","notes","created_at","closed_at"]
    writer.writerow(header)
    for rec in all_recs: writer.writerow([rec.id, rec.asset.value, rec.side.value, rec.status.value, rec.market, rec.entry.value, rec.stop_loss.value, ", ".join(map(str, rec.targets.values)), rec.exit_price, rec.notes, rec.created_at.strftime('%Y-%m-%d %H:%M:%S') if rec.created_at else "", rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""])
    output.seek(0); bytes_buffer = io.BytesIO(output.getvalue().encode('utf-8')); csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    await update.message.reply_document(document=csv_file, caption="تم إنشاء التصدير.")


def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_USER_FILTER))
# --- END OF FILE ---