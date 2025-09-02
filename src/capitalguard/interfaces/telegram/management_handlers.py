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

log = logging.getLogger(__name__)

# --- State Key for Closing Flow ---
AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"

# --- Public Channel Handlers ---

async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        rec_id = int(query.data.split(':')[2])
        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")
        rec = trade_service.repo.get(rec_id)
        if not rec:
            await query.answer("This recommendation seems to be deleted.", show_alert=True)
            return

        live_price = price_service.get_cached_price(rec.asset.value, rec.market)
        if live_price:
            setattr(rec, "live_price", live_price)
        
        new_text = build_trade_card_text(rec)
        new_keyboard = public_channel_keyboard(rec.id)

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
            log.warning(f"BadRequest on public card update for rec {query.data}: {e}")
            await query.answer("An error occurred.", show_alert=True)
    except Exception as e:
        log.error(f"Error updating public card for rec {query.data}: {e}", exc_info=True)
        await query.answer("An error occurred while updating.", show_alert=True)

# --- Analyst Private Control Panel Handlers ---

async def update_private_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = int(query.data.split(':')[2])
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    rec = trade_service.repo.get(rec_id)
    if not rec:
        await query.edit_message_text("This recommendation seems to be deleted.")
        return
    
    live_price = price_service.get_cached_price(rec.asset.value, rec.market)
    if live_price:
        setattr(rec, "live_price", live_price)
    
    new_text = "Panel de control actualizado:\n\n" + build_trade_card_text(rec)
    keyboard = analyst_control_panel_keyboard(rec_id)
    await query.edit_message_text(text=new_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await query.answer("Live price updated!")

async def move_sl_to_be_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Processing: Moving SL to BE...")
    rec_id = int(query.data.split(':')[2])
    trade_service: TradeService = get_service(context, "trade_service")
    updated_rec = trade_service.move_sl_to_be(rec_id)
    
    if updated_rec:
        new_text = "✅ SL moved to Break-Even. Control panel updated:\n\n" + build_trade_card_text(updated_rec)
        keyboard = analyst_control_panel_keyboard(rec_id)
        await query.edit_message_text(text=new_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await query.answer("Action failed. The recommendation might be closed or not found.", show_alert=True)

async def partial_close_note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Processing: Adding partial close note...")
    rec_id = int(query.data.split(':')[2])
    trade_service: TradeService = get_service(context, "trade_service")
    updated_rec = trade_service.add_partial_close_note(rec_id)

    if updated_rec:
        new_text = "✅ Partial close noted. Control panel updated:\n\n" + build_trade_card_text(updated_rec)
        keyboard = analyst_control_panel_keyboard(rec_id)
        await query.edit_message_text(text=new_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await query.answer("Action failed. The recommendation might be closed or not found.", show_alert=True)

async def start_close_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_CLOSE_PRICE_KEY] = rec_id
    await query.answer()
    await query.edit_message_text(
        text=f"{query.message.text}\n\n"
             f"<b>🔻 Please reply to this message with the exit price to close recommendation #{rec_id}.</b>",
        parse_mode=ParseMode.HTML
    )

async def received_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_CLOSE_PRICE_KEY not in context.user_data:
        return # Not in the closing flow, ignore
    
    rec_id = context.user_data[AWAITING_CLOSE_PRICE_KEY]
    try:
        exit_price = float(update.message.text.strip())
        text = f"Confirm closing recommendation <b>#{rec_id}</b> at price <code>{exit_price}</code>?"
        keyboard = confirm_close_keyboard(rec_id, exit_price)
        await update.message.reply_html(text, reply_markup=keyboard)
    except ValueError:
        await update.message.reply_text("⚠️ Invalid price. Please send a valid number.")

async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, _, rec_id_str, exit_price_str = query.data.split(':')
    rec_id = int(rec_id_str)
    exit_price = float(exit_price_str)
    
    await query.answer("Closing recommendation...")
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        rec = trade_service.close(rec_id, exit_price)
        final_text = "✅ Recommendation closed successfully.\n\n" + build_trade_card_text(rec)
        await query.edit_message_text(text=final_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await query.edit_message_text(f"❌ Failed to close recommendation: {e}")
    finally:
        if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = int(query.data.split(':')[2])
    if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    
    trade_service: TradeService = get_service(context, "trade_service")
    rec = trade_service.repo.get(rec_id)
    text = "Close operation cancelled. Returning to control panel:\n\n" + build_trade_card_text(rec)
    keyboard = analyst_control_panel_keyboard(rec_id)
    await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = int(query.data.split(':')[2])
    keyboard = analyst_edit_menu_keyboard(rec_id)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=keyboard)

async def back_to_main_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = int(query.data.split(':')[2])
    keyboard = analyst_control_panel_keyboard(rec_id)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=keyboard)

def register_management_handlers(application: Application):
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
    # (Handlers for edit_sl and edit_tp will be added in the next sprint)

    # Closing Flow
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:"))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price), group=1)
# --- END OF FILE ---