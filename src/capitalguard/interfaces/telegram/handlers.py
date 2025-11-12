# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/handlers.py ---
# src/capitalguard/interfaces/telegram/handlers.py (v27.0 - Image Parsing)
"""
The central function that collects and registers all bot handlers.
✅ THE FIX (ADR-003): Imported and registered the new `image_parsing_handler`.
    - This handler runs in group 1, alongside the text forward handler,
      to catch forwarded photos and initiate the parsing flow.
"""

from telegram.ext import Application

# Import registration functions from each independent handler module.
from .admin_commands import register_admin_commands
from .channel_linking_handler import register_channel_linking_handler
from .conversation_handlers import register_conversation_handlers
from .forward_parsing_handler import register_forward_parsing_handlers
from .management_handlers import register_management_handlers
from .commands import register_commands

# ✅ ADDED (ADR-003): Import the new image handler registration function
from .image_parsing_handler import register_image_parsing_handler


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
    register_forward_parsing_handlers(application)
    
    # ✅ ADDED (ADR-003): Register the new image handler in the same group
    register_image_parsing_handler(application)


    # --- PRIORITY GROUP 1 (or default): GENERAL CALLBACK HANDLERS ---
    # These handle button presses from non-conversational messages, like
    # the management panels for open positions.
    # They can run after commands.
    register_management_handlers(application)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/handlers.py ---