#--- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import uuid
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters, BaseFilter
)
from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.formatting.telegram_templates import format_signal
from .keyboards import confirm_recommendation_keyboard

# ... (باقي الملف مثل STATES والدوال المساعدة يبقى كما هو)
# ... (start_new_recommendation, received_asset, etc.)

# ✅ الدالة الآن تستقبل الفلتر كمعامل
def get_recommendation_conversation_handler(allowed_filter: BaseFilter) -> ConversationHandler:
    """
    Builds the ConversationHandler for creating recommendations.
    """
    return ConversationHandler(
        # ✅ استخدام الفلتر المستلم هنا
        entry_points=[CommandHandler("newrec", start_new_recommendation, filters=filters.ChatType.PRIVATE & allowed_filter)],
        states={
            ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_asset)],
            SIDE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_side)],
            ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_entry)],
            STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_stop_loss)],
            TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_targets)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        persistent=True,
        name="new_recommendation_conversation",
    )
#--- END OF FILE ---