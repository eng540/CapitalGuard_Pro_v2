# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
import logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from .helpers import get_service
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard, public_channel_keyboard
from .ui_texts import build_trade_card_text
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

# --- Public Channel Handlers ---

async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the 'Update Live Data' button press from the public channel.
    """
    query = update.callback_query
    
    try:
        rec_id = int(query.data.split(':')[2])
    except (IndexError, ValueError):
        await query.answer("Error: Invalid recommendation ID.", show_alert=True)
        return

    try:
        # Get services
        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")

        # Fetch the latest recommendation data
        rec = trade_service.repo.get(rec_id) # Using repo directly for a read-only op is fine here
        if not rec:
            await query.answer("This recommendation seems to be deleted.", show_alert=True)
            return

        # Fetch the live price (it will be cached)
        asset = getattr(rec.asset, "value", rec.asset)
        market = getattr(rec, "market", "Futures")
        live_price = price_service.get_cached_price(asset, market)
        
        # Add live price to the recommendation object temporarily for text building
        # This is a bit of a hack, but avoids changing the domain entity for a view concern
        if live_price:
            setattr(rec, "live_price", live_price)
        
        # Re-build the card text and keyboard
        new_text = build_trade_card_text(rec)
        new_keyboard = public_channel_keyboard(rec.id)

        # Check if the message has actually changed to avoid API errors
        if query.message.text != new_text:
            await query.edit_message_text(
                text=new_text,
                reply_markup=new_keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            await query.answer("Data updated!")
        else:
            await query.answer("Data is already up to date.")

    except Exception as e:
        log.error(f"Error updating public card for rec_id {rec_id}: {e}", exc_info=True)
        await query.answer("An error occurred while updating.", show_alert=True)


# --- Analyst Private Control Panel Handlers ---
# ... (The existing handlers for closing recommendations remain here) ...


def register_management_handlers(application: Application):
    # Public handler
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:"))
    
    # Analyst private handlers
    # ... (register the handlers for closing, editing, etc.)
# --- END OF FILE ---