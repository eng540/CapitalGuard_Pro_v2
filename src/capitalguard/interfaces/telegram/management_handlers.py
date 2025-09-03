# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
import logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from .helpers import get_service
from .keyboards import public_channel_keyboard, analyst_control_panel_keyboard, analyst_edit_menu_keyboard, confirm_close_keyboard
from .ui_texts import build_trade_card_text
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.domain.entities import RecommendationStatus

log = logging.getLogger(__name__)

# --- State Key for Conversation-like Flows ---
AWAITING_INPUT_KEY = "awaiting_user_input_for"

# --- Public Channel Handlers ---
async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        rec_id = int(query.data.split(':')[2])
        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")
        rec = trade_service.repo.get(rec_id)
        if not rec:
            await query.answer("This recommendation seems to be deleted.", show_alert=True); return
        if rec.status == RecommendationStatus.CLOSED:
            await query.answer("This trade is already closed.", show_alert=False); return

        asset = rec.asset.value; market = rec.market
        live_price = price_service.get_cached_price(asset, market)
        if not live_price:
            await query.answer("Could not fetch live price. Please try again later.", show_alert=True); return

        if rec.status == RecommendationStatus.PENDING:
            entry_price = rec.entry.value; side = rec.side.value
            is_triggered = (side == 'LONG' and live_price >= entry_price) or \
                           (side == 'SHORT' and live_price <= entry_price)
            if is_triggered:
                rec.activate(); rec = trade_service.repo.update(rec)
                log.info(f"Recommendation #{rec.id} for {asset} has been ACTIVATED at price {live_price}.")
                if rec.user_id and rec.user_id.isdigit():
                    context.bot_data['services']['notifier'].send_private_message(
                        chat_id=int(rec.user_id), rec=rec,
                        text_header=f"🔥 Your recommendation #{rec.id} ({asset}) is now ACTIVE!"
                    )
        setattr(rec, "live_price", live_price)
        new_text = build_trade_card_text(rec)
        new_keyboard = public_channel_keyboard(rec.id)
        try:
            if query.message.text != new_text or str(query.message.reply_markup) != str(new_keyboard):
                await query.edit_message_text(
                    text=new_text, reply_markup=new_keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
                await query.answer("Data updated!")
            else:
                await query.answer("Data is already up to date.")
        except BadRequest as e:
            if "Message is not modified" in str(e):
                await query.answer("Data is already up to date.")
            else: raise e
    except Exception as e:
        log.error(f"Error in update_public_card for rec {query.data}: {e}", exc_info=True)
        try: await query.answer("An error occurred while updating.", show_alert=True)
        except Exception: pass

# --- Analyst Private Control Panel Handlers ---
# ... (All the handlers like update_private_card, move_sl_to_be_handler, etc. are here)
async def update_private_card(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def move_sl_to_be_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def partial_close_note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def start_close_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def back_to_main_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def start_edit_sl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def start_edit_tp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...
async def received_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): #...

# ✅ --- FIX: Ensure this registration function exists and is complete ---
def register_management_handlers(application: Application):
    """Registers all handlers related to managing existing recommendations."""
    # --- Public Channel Handlers ---
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:"))
    
    # --- Analyst Private Control Panel Handlers ---
    # Main Panel
    application.add_handler(CallbackQueryHandler(update_private_card, pattern=r"^rec:update_private:"))
    application.add_handler(CallbackQueryHandler(move_sl_to_be_handler, pattern=r"^rec:move_be:"))
    application.add_handler(CallbackQueryHandler(partial_close_note_handler, pattern=r"^rec:close_partial:"))
    application.add_handler(CallbackQueryHandler(start_close_flow_handler, pattern=r"^rec:close_start:"))
    
    # Edit Sub-menu
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:"))
    application.add_handler(CallbackQueryHandler(back_to_main_panel_handler, pattern=r"^rec:back_to_main:"))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:"))
    application.add_handler(CallbackQueryHandler(start_edit_tp_handler, pattern=r"^rec:edit_tp:"))
    
    # Closing Flow
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:"))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:"))
    
    # Universal Input Handler (must have a higher group number to run after commands)
    # This handler processes replies for closing, editing SL, editing TPs, etc.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_input_handler), group=1)
# --- END OF FILE ---