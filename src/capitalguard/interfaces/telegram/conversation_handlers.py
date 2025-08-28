#--- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import uuid
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.formatting.telegram_templates import format_signal
from .keyboards import confirm_recommendation_keyboard

ASSET, SIDE, ENTRY, STOP_LOSS, TARGETS = range(5)

async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data_key = query.data.split(':')[2]
    rec_data = context.bot_data.get(user_data_key)
    if not rec_data:
        await query.edit_message_text("انتهت صلاحية هذه الجلسة.")
        return

    # ✅ وصول مباشر وموثوق للخدمة
    trade_service = context.application.bot_data.get("trade_service")
    if not isinstance(trade_service, TradeService):
        await query.edit_message_text("❌ خطأ داخلي: خدمة التداول غير مهيأة.")
        return

    try:
        new_rec = trade_service.create(
            asset=rec_data['asset'],
            side=rec_data['side'],
            entry=rec_data['entry'],
            stop_loss=rec_data['stop_loss'],
            targets=rec_data['targets'],
            user_id=str(query.from_user.id)
        )
        # ... (باقي منطق النشر)
    except Exception as e:
        logging.exception("Failed to publish recommendation")
        await query.edit_message_text(f"❌ فشل في النشر: {e}")
    finally:
        if user_data_key in context.bot_data:
            del context.bot_data[user_data_key]

# ... (باقي دوال المحادثة مثل start_new_recommendation, received_asset, etc., تبقى كما هي)
# ... (فقط تأكد من أنها لا تحتوي على أي استدعاءات لـ _ensure_services)
#--- END OF FILE ---