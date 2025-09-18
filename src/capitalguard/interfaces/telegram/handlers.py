# --- START OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.0) ---
# src/capitalguard/interfaces/telegram/handlers.py

from telegram.ext import Application
from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers

def register_all_handlers(application: Application):
    """
    The central function that collects and registers all bot handlers.
    The order of registration is critical for correct behavior, as handlers are
    processed sequentially based on the order they are added.
    """
    
    # 1. Register specific, non-conversation commands first.
    # These handlers (e.g., /start, /help, /channels) are checked first.
    # This ensures they are always executed immediately and are not accidentally
    # caught by the more general ConversationHandler.
    register_commands(application)

    # 2. Register the ConversationHandler for creating recommendations.
    # This handler manages complex, multi-step user interactions. Its entry points
    # (/newrec, /new, etc.) will be matched here. Any other command will pass through.
    # Its fallbacks will only trigger if the user is *already* in a conversation.
    register_conversation_handlers(application)

    # 3. Register general callback query handlers for managing existing recommendations.
    # These are for button interactions on existing trade cards (e.g., "Update Price").
    # They are registered last because they are the most general patterns.
    # The `block=False` argument is used within these handlers to ensure they don't
    # stop other handlers in different groups from processing an update.
    register_management_handlers(application)

# --- END OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.0) ---