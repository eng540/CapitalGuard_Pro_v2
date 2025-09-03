# --- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from telegram.ext import Application
from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers

def register_all_handlers(application: Application):
    """
    The central function that collects and registers all bot handlers.
    The order of registration can be important.
    """
    # Register command handlers (like /start, /help, /open etc.)
    register_commands(application)
    
    # Register the main conversation handler for creating recommendations (/newrec, /settings)
    register_conversation_handlers(application)
    
    # Register handlers for managing existing recommendations (button callbacks etc.)
    # Make sure this doesn't conflict with conversation handlers.
    register_management_handlers(application)
# --- END OF FILE ---