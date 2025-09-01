#--- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import uuid
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters, BaseFilter
)
from .helpers import get_service
from .keyboards import confirm_recommendation_keyboard
from capitalguard.interfaces.telegram.ui_texts import build_review_text

# ... (States and helper functions like _format_recap, start_new_recommendation, etc., can remain)
# ... The validation helpers (_validate_sl_vs_entry, etc.) should be REMOVED from this file.

async def received_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Stores targets and shows a recap for confirmation. No business logic validation here.
    """
    try:
        targets_str = update.message.text.strip().replace(',', ' ').split()
        targets = [float(t) for t in targets_str if t]
        if not targets:
            raise ValueError("No valid numbers found")
        context.user_data['recommendation']['targets'] = targets
    except (ValueError, TypeError):
        await update.message.reply_text("الأهداف غير صالحة. الرجاء إدخال قائمة أرقام صحيحة.")
        return TARGETS
        
    user_data_key = str(uuid.uuid4())
    context.bot_data[user_data_key] = context.user_data.pop('recommendation', {})
    
    recap_text = build_review_text(context.bot_data[user_data_key])
    await update.message.reply_markdown(recap_text, reply_markup=confirm_recommendation_keyboard(user_data_key))
    return ConversationHandler.END

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
        # Call the new unified creation method
        new_rec = trade_service.create_and_publish_recommendation(
            asset=rec_data['asset'],
            side=rec_data['side'],
            market=rec_data.get("market", "Futures"),
            entry=rec_data['entry'],
            stop_loss=rec_data['stop_loss'],
            targets=rec_data['targets'],
            notes=rec_data.get("notes"),
            user_id=str(query.from_user.id)
        )
        await query.edit_message_text(f"✅ تم نشر التوصية #{new_rec.id} بنجاح!")
    except (ValueError, RuntimeError) as e:
        # Catch validation or publishing errors from the service
        await query.edit_message_text(f"❌ فشل في النشر: {e}")
    except Exception:
        # Catch unexpected errors
        logging.exception("Unexpected error during recommendation publishing")
        await query.edit_message_text("❌ حدث خطأ غير متوقع أثناء النشر.")
    finally:
        if user_data_key in context.bot_data:
            del context.bot_data[user_data_key]

# ... (The rest of the file, including get_recommendation_conversation_handler, remains)
#--- END OF FILE ---