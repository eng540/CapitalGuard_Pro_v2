# src/capitalguard/interfaces/telegram/handlers.py (v25.5 - FINAL & DECOUPLED)
"""
The central function that collects and registers all bot handlers.
The order of registration is critical for correct behavior.
"""

from telegram.ext import Application

from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers
from .admin_commands import register_admin_commands
from .forwarding_handlers import create_forwarding_conversation_handler

def register_all_handlers(application: Application):
    """
    Registers all handlers for the Telegram bot in a specific order.
    """
    # Group 0: Admin Commands (Highest Priority)
    register_admin_commands(application)

    # Group 1: Conversational Handlers (e.g., /newrec)
    register_conversation_handlers(application)
    application.add_handler(create_forwarding_conversation_handler())

    # Group 2: Main User Commands (Non-conversational)
    register_commands(application)

    # Group 3: General Callback Query Handlers (Lowest Priority)
    register_management_handlers(application)

#END