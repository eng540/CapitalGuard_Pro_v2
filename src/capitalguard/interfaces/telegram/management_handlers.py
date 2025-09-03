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
    """
    Handles the 'Update Live Data' button press and acts as the state engine for recommendations.
    """
    query = update.callback_query
    try:
        rec_id = int(query.data.split(':')[2])
        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")
        
        rec = trade_service.repo.get(rec_id)
        if not rec:
            await query.answer("This recommendation seems to be deleted.", show_alert=True)
            return
        
        # If the trade is already closed, no need to fetch price. Just inform the user.
        if rec.status == RecommendationStatus.CLOSED:
            await query.answer("This trade is already closed.", show_alert=False)
            return

        asset = rec.asset.value
        market = rec.market
        live_price = price_service.get_cached_price(asset, market)

        if not live_price:
            await query.answer("Could not fetch live price. Please try again later.", show_alert=True)
            return

        # ✅ --- STATE ENGINE LOGIC ---
        # Check if a PENDING trade should be activated
        if rec.status == RecommendationStatus.PENDING:
            entry_price = rec.entry.value
            side = rec.side.value
            is_triggered = (side == 'LONG' and live_price >= entry_price) or \
                           (side == 'SHORT' and live_price <= entry_price)
            
            if is_triggered:
                rec.activate()
                rec = trade_service.repo.update(rec)
                log.info(f"Recommendation #{rec.id} for {asset} has been ACTIVATED at price {live_price}.")
                # Send an alert to the analyst
                if rec.user_id and rec.user_id.isdigit():
                    context.bot_data['services']['notifier'].send_private_message(
                        chat_id=int(rec.user_id),
                        rec=rec,
                        text_header=f"🔥 Your recommendation #{rec.id} ({asset}) is now ACTIVE!"
                    )

        # Attach live price for the UI text builder
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
            else:
                raise e # Re-raise other bad requests
    except Exception as e:
        log.error(f"Error in update_public_card for rec {query.data}: {e}", exc_info=True)
        try:
            await query.answer("An error occurred while updating.", show_alert=True)
        except Exception:
            pass # Ignore if the query is too old
            
# --- (The rest of the file: Analyst Private Handlers and Registration, remain unchanged from Sprint 3) ---
# ...
# --- END OF FILE ---