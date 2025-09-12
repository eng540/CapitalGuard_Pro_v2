# --- START OF FULL, RE-ARCHITECTED, AND FINAL FILE ---
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
    # dedicated handlers before the more general ConversationHandler can
    # intercept them as unexpected input.
    register_commands(application)

    # 2. Register callback query handlers for button interactions.
    # These are also highly specific and should be checked before the conversation
    # fallbacks. The `block=False` parameter inside these handlers is also
    # crucial to prevent them from stopping updates from reaching the conversation.
    register_management_handlers(application)

    # 3. Register the ConversationHandler last.
    # Since its fallbacks can catch any command or message, it should be
    # given a chance to process an update only after all more specific handlers
    # have been checked.
    register_conversation_handlers(application)

# --- END OF FULL, RE-ARCHITECTED, AND FINAL FILE ---