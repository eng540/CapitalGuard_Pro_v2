# --- START OF FINAL, COMPLETE, AND SIMPLIFIED FILE (Version 13.2.1) ---
# src/capitalguard/interfaces/telegram/handlers.py

from telegram.ext import Application

from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers
from .admin_commands import register_admin_commands

def register_all_handlers(application: Application):
    """
    The central function that collects and registers all bot handlers.
    The order of registration is critical for correct behavior.
    """
    
    # The logic to ensure a user exists has been moved into the /start command
    # and the @require_active_user decorator, making a global filter unnecessary
    # and solving the NameError.

    # Group 0: Admin Commands (Highest Priority)
    register_admin_commands(application)

    # Group 1: Main User Commands (Non-conversational)
    # The /start command within this group will now handle user creation.
    register_commands(application)

    # Group 2: Conversational Handlers
    # These are protected by decorators that assume the user already exists.
    register_conversation_handlers(application)

    # Group 3: General Callback Query Handlers (Lowest Priority)
    register_management_handlers(application)

# --- END OF FINAL, COMPLETE, AND SIMPLIFIED FILE ---