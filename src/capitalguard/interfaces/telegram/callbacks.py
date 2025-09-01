#--- START OF FILE: src/capitalguard/interfaces/telegram/callbacks.py ---
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler
from .helpers import get_service
from .keyboards import confirm_close_keyboard

AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"

async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_CLOSE_PRICE_KEY] = rec_id
    await query.edit_message_text(f"🔻 أرسل الآن سعر الخروج لإغلاق التوصية #{rec_id}.")

async def confirm_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(':')
    rec_id = int(parts[2])
    exit_price = float(parts[3])
    
    trade_service = get_service(context, "trade_service")
    try:
        rec = trade_service.close(rec_id, exit_price)
        await query.edit_message_text(f"✅ تم إغلاق التوصية <b>#{rec.id}</b>.", parse_mode=ParseMode.HTML)
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
    finally:
        if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = int(query.data.split(':')[2])
    if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    await query.edit_message_text("تم التراجع عن الإغلاق.")

def register_callbacks(app):
    app.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:"))
    app.add_handler(CallbackQueryHandler(confirm_close, pattern=r"^rec:confirm_close:"))
    app.add_handler(CallbackQueryHandler(cancel_close, pattern=r"^rec:cancel_close:"))
#--- END OF FILE ---