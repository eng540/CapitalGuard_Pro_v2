# --- START OF FINAL, COMPLETE, AND MONETIZATION-READY FILE (Version 13.0.0) ---
# src/capitalguard/interfaces/telegram/handlers.py

from telegram.ext import Application

from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers
# ✅ NEW: Import the admin command registrar
from .admin_commands import register_admin_commands

def register_all_handlers(application: Application):
    """
    The central function that collects and registers all bot handlers.
    The order of registration is critical for correct behavior. Handlers are
    processed in the order they are added.
    """
    
    # Group 0: Admin Commands
    # These are registered first with a strict filter. They have the highest priority
    # to ensure admin commands are always processed and never intercepted by other handlers.
    register_admin_commands(application)

    # Group 1: Main User Commands (Non-conversational)
    # These handlers (e.g., /start, /help, /stats) are checked next.
    # They are protected by the main access control filter.
    register_commands(application)

    # Group 2: Conversational Handlers
    # These handlers manage complex, multi-step user interactions like creating a new
    # recommendation. Their entry points (/newrec, /new, etc.) will be matched here.
    register_conversation_handlers(application)

    # Group 3: General Callback Query Handlers
    # These are for button interactions on existing trade cards (e.g., "Update Price").
    # They are registered last to ensure that any callback query that is part of an
    # active conversation is handled by the conversation handler first.
    register_management_handlers(application)

# --- END OF FINAL, COMPLETE, AND MONETIZATION-READY FILE ---