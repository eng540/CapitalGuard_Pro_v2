# --- START OF FINAL, CORRECTED, AND PRODUCTION-READY FILE (Version 8.0.4) ---
# src/capitalguard/interfaces/telegram/handlers.py

from telegram.ext import Application
from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers

def register_all_handlers(application: Application):
    """
    The central function that collects and registers all bot handlers.
    The order of registration is critical for correct behavior. Handlers are
    checked in the order they are added.
    """
    
    # 1. Register specific, non-conversation commands first.
    # This ensures that commands like /stats, /open, /help are caught by their
    # dedicated handlers.
    register_commands(application)

    # 2. Register the ConversationHandler next.
    # Since its fallbacks can catch any command or message, it should be
    # given a chance to process an update only after the most specific command
    # handlers have been checked. This is the new, corrected order.
    register_conversation_handlers(application)

    # 3. Register general callback query handlers for button interactions last.
    # These are less specific than conversation states and should only be checked
    # if the update is not part of an active conversation. The `block=False`
    # parameter inside these handlers is crucial to prevent them from stopping
    # updates from reaching other handlers in different groups.
    register_management_handlers(application)

# --- END OF FINAL, CORRECTED, AND PRODUCTION-READY FILE (Version 8.0.4) ---