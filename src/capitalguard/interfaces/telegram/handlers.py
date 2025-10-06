# src/capitalguard/interfaces/telegram/handlers.py (v14.0.0 - Final)

from telegram.ext import Application

from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers
from .admin_commands import register_admin_commands
from .forwarding_handlers import create_forwarding_conversation_handler

def register_all_handlers(application: Application):
    """
    The central function that collects and registers all bot handlers.
    The order of registration is critical for correct behavior.
    """
    
    # Group 0: Admin Commands (Highest Priority)
    register_admin_commands(application)

    # Group 1: Forwarding Conversation Handler (High Priority)
    application.add_handler(create_forwarding_conversation_handler())

    # Group 2: Main User Commands (Non-conversational)
    register_commands(application)

    # Group 3: Analyst-specific Conversational Handlers
    register_conversation_handlers(application)

    # Group 4: General Callback Query Handlers (Lowest Priority)
    register_management_handlers(application)