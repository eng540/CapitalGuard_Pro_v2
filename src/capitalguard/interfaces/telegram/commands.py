#--- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
import io
import csv
from telegram import Update, InputFile
from telegram.ext import Application, ContextTypes, CommandHandler
from .helpers import get_service
from .keyboards import recommendation_management_keyboard
from .auth import ALLOWED_FILTER
from .ui_texts import build_analyst_stats_text
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "• <code>/newrec</code> — Start a conversation to create a recommendation.\n"
        "• <code>/open</code> — View and manage open recommendations.\n"
        "• <code>/stats</code> — View your performance summary.\n"
        "• <code>/export</code> — Export all your recommendations as a CSV file."
    )

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service: TradeService = get_service(context, "trade_service")
    # In a single-analyst setup, we don't need to filter by user_id yet.
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("There are no open recommendations.")
        return
    
    await update.message.reply_text("Here are your open recommendations:")
    for it in items:
        text = (f"<b>#{it.id}</b> — <b>{it.asset.value}</b> ({it.side.value}) | Status: {it.status}")
        # Note: The control panel is now sent privately upon creation.
        # This command is just for listing them.
        await update.message.reply_html(text)

# ✅ --- NEW COMMAND HANDLERS ---

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a summary of the analyst's performance."""
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    # In a single-analyst setup, all stats belong to the one user.
    stats = analytics_service.performance_summary()
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports all recommendation data to a CSV file."""
    await update.message.reply_text("Generating your data export, this may take a moment...")
    
    trade_service: TradeService = get_service(context, "trade_service")
    all_recs = trade_service.list_all()

    if not all_recs:
        await update.message.reply_text("No recommendations found to export.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    header = [
        "id", "asset", "side", "status", "market", "entry_price", "stop_loss", 
        "targets", "exit_price", "notes", "created_at", "closed_at"
    ]
    writer.writerow(header)
    
    # Write data rows
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
    # Create a bytes buffer to send the file
    bytes_buffer = io.BytesIO(output.getvalue().encode('utf-8'))
    
    # Create an InputFile object
    csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    
    await update.message.reply_document(document=csv_file, caption="Here is your data export.")

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_FILTER))
# --- END OF FILE ---