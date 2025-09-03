# --- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
import io
import csv
from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ContextTypes, CommandHandler, ConversationHandler
from .helpers import get_service
from .auth import ALLOWED_FILTER
from .ui_texts import build_analyst_stats_text
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService

# --- Conversation Entry Points & State Keys ---
# We will define states for the new unified flow
CHOOSE_METHOD, INTERACTIVE_BUILDER, QUICK_COMMAND, TEXT_EDITOR = range(4)
USER_PREFERENCE_KEY = "preferred_creation_method"

# --- Keyboards ---
def main_creation_keyboard() -> InlineKeyboardMarkup:
    """The main keyboard to choose the creation method."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي (Guiado)", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع (Rápido)", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي (Pegar)", callback_data="method_editor")],
    ])

def change_method_keyboard() -> InlineKeyboardMarkup:
    """A simple keyboard to allow changing the preferred method."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تغيير طريقة الإدخال", callback_data="change_method")]])

# --- Command Handlers ---

async def newrec_entry_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    The single, smart entry point for creating a recommendation.
    It either shows the method choice or jumps to the user's preferred method.
    """
    preferred_method = context.user_data.get(USER_PREFERENCE_KEY)

    if preferred_method == "interactive":
        await update.message.reply_text("🚀 Starting Interactive Builder (your preferred method)...")
        # Here you would start the interactive conversation handler logic
        # For now, we'll just prompt. This will be fully implemented in the conversation handler.
        await update.message.reply_text("Please send the asset symbol (e.g., BTCUSDT).", reply_markup=change_method_keyboard())
        return INTERACTIVE_BUILDER # Transition to the interactive flow state
    elif preferred_method == "quick":
        await update.message.reply_text(
            "⚡️ Quick Command mode (your preferred method).\n\n"
            "Send your recommendation in a single message starting with `/rec`.\n"
            "Example: `/rec BTCUSDT LONG 65000 64000 66k`",
            reply_markup=change_method_keyboard()
        )
        return QUICK_COMMAND # Transition to a state that waits for the /rec command
    elif preferred_method == "editor":
        await update.message.reply_text(
            "📋 Text Editor mode (your preferred method).\n\n"
            "Paste your recommendation text, starting each field on a new line (e.g., `Asset: BTCUSDT`).",
            reply_markup=change_method_keyboard()
        )
        return TEXT_EDITOR # Transition to the text editor state
    
    # If no preference is set, show the main choice keyboard
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
    # ... (this function's logic remains the same)
    await update.message.reply_text("Generating your data export...")
    trade_service: TradeService = get_service(context, "trade_service")
    all_recs = trade_service.list_all()
    if not all_recs:
        await update.message.reply_text("No recommendations found."); return
    output = io.StringIO(); writer = csv.writer(output)
    header = ["id", "asset", "side", "status", "market", "entry_price", "stop_loss", "targets", "exit_price", "notes", "created_at", "closed_at"]
    writer.writerow(header)
    for rec in all_recs:
        row = [rec.id, rec.asset.value, rec.side.value, rec.status, rec.market, rec.entry.value, rec.stop_loss.value, ", ".join(map(str, rec.targets.values)), rec.exit_price, rec.notes, rec.created_at.strftime('%Y-%m-%d %H:%M:%S'), rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""]
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

    # Note: The main /newrec command is now the entry point to a conversation,
    # so it will be registered in the conversation_handlers file.
    # The /settings command will also be part of this conversation to manage the state.
# --- END OF FILE ---