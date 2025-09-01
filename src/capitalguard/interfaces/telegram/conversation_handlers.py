#--- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import uuid
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import Application, ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters, BaseFilter
from .helpers import get_service # ✅ يستخدم الطريقة الصحيحة
from .keyboards import confirm_recommendation_keyboard
from .ui_texts import build_review_text

# ... (States and conversation flow functions remain the same) ...

async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ...
    trade_service = get_service(context, "trade_service") # ✅ يستخدم الطريقة الصحيحة
    # ... (rest of the function)

def get_recommendation_conversation_handler(allowed_filter: BaseFilter) -> ConversationHandler:
    # ... (returns the ConversationHandler)
    pass

def register_conversation_handlers(app: Application):
    from .auth import ALLOWED_FILTER
    app.add_handler(get_recommendation_conversation_handler(ALLOWED_FILTER))
    app.add_handler(CallbackQueryHandler(publish_recommendation, pattern=r"^rec:publish:"))
    # ... (register other conversation-related callbacks)
#--- END OF FILE ---