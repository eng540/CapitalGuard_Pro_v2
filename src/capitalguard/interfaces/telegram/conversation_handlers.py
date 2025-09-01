#--- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import uuid
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters, BaseFilter
)
from .helpers import get_service
from .keyboards import confirm_recommendation_keyboard

# ... (States, _format_recap, start_new_recommendation, and other conversation steps remain the same)

async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data_key = query.data.split(':')[2]
    rec_data = context.bot_data.get(user_data_key)
    if not rec_data:
        await query.edit_message_text("انتهت صلاحية هذه الجلسة.")
        return

    trade_service = get_service(context, "trade_service")
    try:
        new_rec = trade_service.create(
            # ... create recommendation data from rec_data ...
        )
        await query.edit_message_text(f"✅ تم إنشاء ونشر التوصية #{new_rec.id} بنجاح!")
    except Exception as e:
        logging.exception("Failed to publish recommendation")
        await query.edit_message_text(f"❌ فشل في النشر: {e}")
    finally:
        if user_data_key in context.bot_data:
            del context.bot_data[user_data_key]

# ... (cancel_publication and the full get_recommendation_conversation_handler function)
#--- END OF FILE ---