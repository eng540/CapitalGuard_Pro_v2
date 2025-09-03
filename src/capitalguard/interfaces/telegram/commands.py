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

# ---------------------------------------------------------------------
# Conversation state IDs (must match conversation_handlers.py exactly)
# conversation_handlers defines:
# (CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)  -> 0,1,2
# then I_ASSET_CHOICE is the first interactive state -> 3
# ---------------------------------------------------------------------
CHOOSE_METHOD   = 0
QUICK_COMMAND   = 1
TEXT_EDITOR     = 2
I_ASSET_CHOICE  = 3

USER_PREFERENCE_KEY = "preferred_creation_method"

# --- Keyboards ---
def main_creation_keyboard() -> InlineKeyboardMarkup:
    """Main keyboard to choose the creation method."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def change_method_keyboard() -> InlineKeyboardMarkup:
    """Allow switching the preferred method."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تغيير طريقة الإدخال", callback_data="change_method")]])

# --- Command Handlers ---

async def newrec_entry_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Smart entry point for creating a recommendation.
    Returns the exact state expected by ConversationHandler.
    """
    preferred_method = context.user_data.get(USER_PREFERENCE_KEY)

    if preferred_method == "interactive":
        # Hand over directly to interactive builder's first step (asset choice)
        await update.message.reply_text(
            "🚀 Starting Interactive Builder (your preferred method)...",
            reply_markup=change_method_keyboard()
        )
        # The conversation_handlers sets I_ASSET_CHOICE to 3 — we return that value.
        return I_ASSET_CHOICE

    if preferred_method == "quick":
        await update.message.reply_text(
            "⚡️ وضع الأمر السريع (المفضّل لديك).\n\n"
            "أرسل التوصية برسالة واحدة تبدأ بـ /rec.\n"
            "مثال: /rec BTCUSDT LONG 65000 64000 66k",
            reply_markup=change_method_keyboard()
        )
        return QUICK_COMMAND

    if preferred_method == "editor":
        await update.message.reply_text(
            "📋 وضع المحرّر النصي (المفضّل لديك).\n\n"
            "ألصق التوصية كسطور منظّمة (مثال: Asset: BTCUSDT)",
            reply_markup=change_method_keyboard()
        )
        return TEXT_EDITOR

    # No preference yet -> show method chooser
    await update.message.reply_text(
        "🚀 إنشاء توصية جديدة.\n\nاختر طريقتك المفضلة للإدخال:",
        reply_markup=main_creation_keyboard()
    )
    return CHOOSE_METHOD

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "• <code>/newrec</code> — The main command to create a new recommendation.\n"
        "• <code>/open</code> — View a list of your open recommendations.\n"
        "• <code>/stats</code> — View your performance summary.\n"
        "• <code>/export</code> — Export all your recommendations as a CSV file.\n"
        "• <code>/settings</code> — Manage your preferences."
    )

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service: TradeService = get_service(context, "trade_service")
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("There are no open recommendations.")
        return

    response_lines = ["<b>Your Open Recommendations:</b>"]
    for it in items:
        response_lines.append(f"• #{it.id} — {it.asset.value} ({it.side.value})")
    await update.message.reply_html("\n".join(response_lines))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    stats = analytics_service.performance_summary()
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Generating your data export...")
    trade_service: TradeService = get_service(context, "trade_service")
    all_recs = trade_service.list_all()
    if not all_recs:
        await update.message.reply_text("No recommendations found.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        "id", "asset", "side", "status", "market",
        "entry_price", "stop_loss", "targets", "exit_price",
        "notes", "created_at", "closed_at"
    ]
    writer.writerow(header)

    for rec in all_recs:
        row = [
            rec.id,
            rec.asset.value,
            rec.side.value,
            rec.status,
            rec.market,
            rec.entry.value,
            rec.stop_loss.value,
            ", ".join(map(str, rec.targets.values)),
            rec.exit_price,
            rec.notes,
            rec.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""
        ]
        writer.writerow(row)

    output.seek(0)
    bytes_buffer = io.BytesIO(output.getvalue().encode('utf-8'))
    csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    await update.message.reply_document(document=csv_file, caption="Here is your data export.")

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows user to change their preferred creation method."""
    await update.message.reply_text(
        "⚙️ Settings\n\n"
        "Choose your preferred default method for the `/newrec` command:",
        reply_markup=main_creation_keyboard()
    )
    return CHOOSE_METHOD

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_FILTER))
    # /newrec و /settings يتم ربطهما داخل conversation_handlers كجزء من محادثة واحدة.
# --- END OF FILE ---