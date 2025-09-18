# --- START OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.4) ---
# src/capitalguard/interfaces/telegram/handlers.py

from telegram.ext import Application

from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers

def register_all_handlers(application: Application):
    """
    The central function that collects and registers all bot handlers.
    The order of registration is critical for correct behavior, as handlers are
    processed sequentially based on the order they are added. An update is
    processed by the first handler in the group that matches it.
    """
    
    # Group 0: Specific, non-conversational commands.
    # These handlers (e.g., /start, /help, /channels) are checked first.
    # This ensures they are always executed immediately and are not accidentally
    # caught by the more general ConversationHandler as an unexpected command.
    register_commands(application)

    # Group 0: The ConversationHandler for creating recommendations.
    # This handler manages complex, multi-step user interactions. Its entry points
    # (/newrec, /new, etc.) will be matched here. Any other command that is not an
    # entry point will be ignored and passed to the next handlers.
    # Its fallbacks will only trigger if the user is *already* in a conversation.
    register_conversation_handlers(application)

    # Group 0: General callback query handlers for managing existing recommendations.
    # These are for button interactions on existing trade cards (e.g., "Update Price").
    # They are registered after the ConversationHandler to ensure that any callback query
    # that is part of an active conversation is handled by the conversation first.
    # The `block=False` parameter is used within these handlers' registration to prevent
    # them from stopping other handlers in different groups from processing an update.
    register_management_handlers(application)

# --- END OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.4) ---