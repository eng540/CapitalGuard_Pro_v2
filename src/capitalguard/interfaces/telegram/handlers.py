# File: src/capitalguard/interfaces/telegram/handlers.py
# Version: v30.0.0-R2 (Cleaned Registrar)
# ✅ THE FIX: (R2 Architecture)
#    - 1. (CLEAN) هذا الملف هو الآن "المُسجّل" (Registrar) النظيف.
#    - 2. (Consolidated) لا يزال يستدعي `register_conversation_handlers`
#       و `register_management_handlers`، وهو يعتمد الآن على هيكلتها الداخلية الجديدة
#       التي تفصل بين (Stateful) و (Stateless).
# 🎯 IMPACT: نقطة دخول التسجيل الآن نظيفة ومتوافقة مع "الأرض الواسعة".

from telegram.ext import Application

# Import registration functions from each independent handler module.
from .admin_commands import register_admin_commands
from .channel_linking_handler import register_channel_linking_handler
from .conversation_handlers import register_conversation_handlers
from .forward_parsing_handler import register_forward_parsing_handlers
from .historical_forwarding_handler import register_historical_forwarding_handlers
from .management_handlers import register_management_handlers
from .commands import register_commands
from .log_handler import register_log_handler

def register_all_handlers(application: Application):
    """
    Registers all handlers for the Telegram bot in a specific, logical order.
    The order is crucial:
    Group 0: Conversations & implicit state handlers (highest priority)
    Group 1: Stateless callbacks & message handlers (run if no convo is active)
    """
    
    # --- PRIORITY GROUP 0: ADMIN COMMANDS ---
    register_admin_commands(application) # (Group 0 by default)

    # --- PRIORITY GROUP 0: CONVERSATIONAL HANDLERS ---
    # (R2): This now registers ALL stateful conversations
    # (Creation, Partial Close, User Close, Reply Handlers)
    register_historical_forwarding_handlers(application) # (Group 0: historical staging)
    register_conversation_handlers(application) # (Group 0)
    register_log_handler(application) # (Group 0)
    
    register_channel_linking_handler(application) # (Group 0)

    # --- PRIORITY GROUP 0: SIMPLE COMMANDS ---
    # (Excludes /myportfolio which is now the main entry for management)
    register_commands(application) # (Group 0)

    # --- PRIORITY GROUP 1: SPECIALIZED MESSAGE HANDLERS ---
    # (Runs after Group 0 conversations)
    register_forward_parsing_handlers(application) # (Group 1)

    # --- PRIORITY GROUP 1: STATELESS CALLBACK HANDLERS ---
    # (R2): This now *only* registers stateless navigation and
    # immediate action callbacks. /myportfolio (command) is also here.
    register_management_handlers(application) # (Group 0 for Command, Group 1 for Callbacks)