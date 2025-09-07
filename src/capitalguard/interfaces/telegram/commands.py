#// --- START: src/capitalguard/interfaces/telegram/commands.py ---
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler

from .helpers import get_service
from .auth import ALLOWED_USER_FILTER
from .ui_texts import build_analyst_stats_text
from .keyboards import build_open_recs_keyboard

from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greets the user and implicitly creates their user record via the auth filter."""
    await update.message.reply_html(f"👋 Welcome, {update.effective_user.first_name}! Use /help to see available commands.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "• <code>/newrec</code> — Create a new recommendation.\n"
        "• <code>/open</code> — View your open recommendations.\n"
        "• <code>/stats</code> — View your performance summary."
    )

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the user's open recommendations."""
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    user_id = update.effective_user.id

    items = trade_service.repo.list_open_for_user(user_id)
    
    if not items:
        await update.message.reply_text("✅ You have no open recommendations.")
        return
        
    keyboard = build_open_recs_keyboard(items, current_page=1, price_service=price_service)
    header_text = "<b>📊 Your Open Recommendations Dashboard</b>"
    await update.message.reply_html(
        f"{header_text}\nSelect a recommendation to manage it:",
        reply_markup=keyboard
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the user's personal performance stats."""
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    user_id = str(update.effective_user.id) # Analytics service expects string

    # This service needs to be updated to be user-aware
    # For now, we call a new user-specific method
    stats = analytics_service.performance_summary_for_user(user_id) # We will need to create this method
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)

def register_commands(app: Application):
    # The ALLOWED_USER_FILTER now ensures a user record exists for every command
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_USER_FILTER))
// --- END: src/capitalguard/interfaces/telegram/commands.py ---```

---

**4. Complete File: `src/capitalguard/interfaces/telegram/handlers.py`**
*(Reason for change: The old registration callback is no longer needed, as the auth filter handles user creation implicitly and automatically.)*

```python
// --- START: src/capitalguard/interfaces/telegram/handlers.py ---
from telegram.ext import Application
from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers

def register_all_handlers(application: Application):
    """The central function that collects and registers all bot handlers."""
    # Register standard command handlers (/start, /help, /open etc.)
    register_commands(application)
    
    # Register the main conversation handler for creating recommendations (/newrec)
    register_conversation_handlers(application)
    
    # Register handlers for managing existing recommendations (button callbacks etc.)
    register_management_handlers(application)
#// --- END: src/capitalguard/interfaces/telegram/handlers.py ---