#--- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from telegram.ext import Application
from .commands import register_commands
from .conversation_handlers import register_conversation_handlers
from .management_handlers import register_management_handlers

def register_all_handlers(application: Application):
    """
    الدالة المركزية التي تجمع وتسجل جميع معالجات البوت.
    """
    register_commands(application)
    register_conversation_handlers(application)
    register_management_handlers(application)
#--- END OF FILE ---