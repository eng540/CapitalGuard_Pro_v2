# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/handlers.py ---
# src/capitalguard/interfaces/telegram/handlers.py (v28.0 - Unified Parsing)
"""
The central function that collects and registers all bot handlers.
✅ THE FIX (ADR-003 / NameError fix):
    - Removed the import and registration call for the deleted
      `register_image_parsing_handler`.
    - The `register_forward_parsing_handlers` now correctly handles
      both text and image entry points for the unified parsing conversation.
"""

from telegram.ext import Application

# Import registration functions from each independent handler module.
from .admin_commands import register_admin_commands
from .channel_linking_handler import register_channel_linking_handler
from .conversation_handlers import register_conversation_handlers
from .forward_parsing_handler import register_forward_parsing_handlers
from .management_handlers import register_management_handlers
from .commands import register_commands

# ❌ REMOVED (ADR-003): No longer need a separate image handler
# from .image_parsing_handler import register_image_parsing_handler


def register_all_handlers(application: Application):
    """
    Registers all handlers for the Telegram bot in a specific, logical order
    to ensure correct priority and execution flow.
    The order is crucial for
    the proper functioning of conversations and priority handling.
    """
    
    # --- PRIORITY GROUP 0: ADMIN COMMANDS ---
    # These handlers run first, allowing administrators to perform actions
    # regardless of any other user state or active conversations.
    register_admin_commands(application)

    # --- PRIORITY GROUP 0: CONVERSATIONAL HANDLERS ---
    # Conversations must be registered before general message handlers
    # so they can capture user input first when a conversation is active.
    # The `group` number for handlers inside these modules is 0 by default.
    register_conversation_handlers(application)
    register_channel_linking_handler(application)

    # --- PRIORITY GROUP 0: SIMPLE COMMANDS ---
    # These are the primary, single-shot commands like /start, /help, etc.
    # They should also have high priority to be accessible at any time.
    register_commands(application)

    # --- PRIORITY GROUP 1: SPECIALIZED MESSAGE HANDLERS ---
    # This group is for handlers that should only run if no conversation
    # is currently active.
    # The `group=1` in the handler's registration
    # ensures it runs after all default group 0 handlers have been checked.
    
    # ✅ (ADR-003): This function now registers handlers for BOTH text and photos.
    register_forward_parsing_handlers(application)
    
    # ❌ REMOVED (ADR-003): This is now merged into the function above.
    # register_image_parsing_handler(application)


    # --- PRIORITY GROUP 1 (or default): GENERAL CALLBACK HANDLERS ---
    # These handle button presses from non-conversational messages, like
    # the management panels for open positions.
    # They can run after commands.
    register_management_handlers(application)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/handlers.py ---