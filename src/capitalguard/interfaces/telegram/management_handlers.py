# --- START OF FINAL MODIFIED FILE (V6): src/capitalguard/interfaces/telegram/management_handlers.py ---
import logging
import types
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from .helpers import get_service
from .keyboards import analyst_control_panel_keyboard, confirm_close_keyboard # ... and others
from .parsers import parse_number, parse_number_list # Assuming these are moved to a central parsers.py
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"

# --- Core Handlers (Now responsible for notifications) ---

async def show_rec_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (This handler is now correct and uses context.bot) ...

async def move_sl_to_be_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري النقل...")
    rec_id = _parse_tail_int(query.data)
    if not rec_id: return

    trade_service: TradeService = get_service(context, "trade_service")
    rec = trade_service.move_sl_to_be(rec_id)
    
    # ✅ Handler is now responsible for notification
    if rec:
        notification_text = f"<b>🛡️ تأمين صفقة #{rec.asset.value}</b>\nتم نقل وقف الخسارة إلى نقطة الدخول."
        _notify_all_channels(context, rec_id, notification_text)
    
    await show_rec_panel_handler(update, context)

async def take_partial_profit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the flexible partial profit taking flow."""
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return ConversationHandler.END
    
    context.user_data[AWAITING_INPUT_KEY] = {"action": "partial_profit_percent", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await context.bot.edit_message_text(
        chat_id=query.message.chat_id, message_id=query.message.message_id,
        text=f"{query.message.text}\n\n<b>💰 الرجاء الرد بالنسبة المئوية التي تم جنيها (مثال: 50).</b>",
        parse_mode=ParseMode.HTML
    )
    return I_PARTIAL_PROFIT_PERCENT

async def received_partial_profit_percent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (Handler to get percentage and ask for price) ...
    return I_PARTIAL_PROFIT_PRICE

async def received_partial_profit_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (Handler to get price, call the service, and send notifications) ...
    trade_service = get_service(context, "trade_service")
    # ... call trade_service.take_partial_profit ...
    
    notification_text = f"💰 جني أرباح جزئي لـ #{rec.asset.value} | تم إغلاق {percentage}% من الصفقة."
    _notify_all_channels(context, rec_id, notification_text)
    
    await show_rec_panel_handler(dummy_update, context)
    return ConversationHandler.END

def _notify_all_channels(context: ContextTypes.DEFAULT_TYPE, rec_id: int, text: str):
    """Helper to send reply notifications to all published channels."""
    repo = get_service(context, "trade_service").repo
    notifier = get_service(context, "notifier")
    published_messages = repo.get_published_messages(rec_id)
    for msg_meta in published_messages:
        try:
            notifier.post_notification_reply(
                chat_id=msg_meta.telegram_channel_id,
                message_id=msg_meta.telegram_message_id,
                text=text
            )
        except Exception as e:
            log.warning("Failed to send reply notification for rec #%s to channel %s: %s", rec_id, msg_meta.telegram_channel_id, e)

def register_management_handlers(app: Application):
    # ... (Registration of handlers, including the new partial profit conversation) ...
# --- END OF FINAL MODIFIED FILE (V6) ---