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

# --- State Keys for Conversation-like Flows ---
AWAITING_INPUT_KEY = "awaiting_user_input_for"
# Format: context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_sl", "rec_id": 123}

# --- Public Channel Handlers ---
async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (unchanged)
    query = update.callback_query
    try:
        rec_id = int(query.data.split(':')[2]); trade_service: TradeService = get_service(context, "trade_service"); price_service: PriceService = get_service(context, "price_service")
        rec = trade_service.repo.get(rec_id)
        if not rec: await query.answer("This recommendation seems to be deleted.", show_alert=True); return
        live_price = price_service.get_cached_price(rec.asset.value, rec.market)
        if live_price: setattr(rec, "live_price", live_price)
        new_text = build_trade_card_text(rec); new_keyboard = public_channel_keyboard(rec.id)
        if query.message.text != new_text or str(query.message.reply_markup) != str(new_keyboard):
            await query.edit_message_text(text=new_text, reply_markup=new_keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            await query.answer("Data updated!")
        else: await query.answer("Data is already up to date.")
    except BadRequest as e:
        if "Message is not modified" in str(e): await query.answer("Data is already up to date.")
        else: log.warning(f"BadRequest on public card update for rec {query.data}: {e}"); await query.answer("An error occurred.", show_alert=True)
    except Exception as e: log.error(f"Error updating public card for rec {query.data}: {e}", exc_info=True); await query.answer("An error occurred while updating.", show_alert=True)

# --- Analyst Private Control Panel Handlers ---
async def update_private_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (unchanged)
    query = update.callback_query; rec_id = int(query.data.split(':')[2]); trade_service: TradeService = get_service(context, "trade_service"); price_service: PriceService = get_service(context, "price_service")
    rec = trade_service.repo.get(rec_id)
    if not rec: await query.edit_message_text("This recommendation seems to be deleted."); return
    live_price = price_service.get_cached_price(rec.asset.value, rec.market);
    if live_price: setattr(rec, "live_price", live_price)
    new_text = "Panel de control actualizado:\n\n" + build_trade_card_text(rec); keyboard = analyst_control_panel_keyboard(rec_id)
    await query.edit_message_text(text=new_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True); await query.answer("Live price updated!")
async def move_sl_to_be_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (unchanged)
    query = update.callback_query; await query.answer("Processing: Moving SL to BE..."); rec_id = int(query.data.split(':')[2]); trade_service: TradeService = get_service(context, "trade_service")
    updated_rec = trade_service.move_sl_to_be(rec_id)
    if updated_rec:
        new_text = "✅ SL moved to Break-Even. Control panel updated:\n\n" + build_trade_card_text(updated_rec); keyboard = analyst_control_panel_keyboard(rec_id)
        await query.edit_message_text(text=new_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else: await query.answer("Action failed. The recommendation might be closed or not found.", show_alert=True)
async def partial_close_note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (unchanged)
    query = update.callback_query; await query.answer("Processing: Adding partial close note..."); rec_id = int(query.data.split(':')[2]); trade_service: TradeService = get_service(context, "trade_service")
    updated_rec = trade_service.add_partial_close_note(rec_id)
    if updated_rec:
        new_text = "✅ Partial close noted. Control panel updated:\n\n" + build_trade_card_text(updated_rec); keyboard = analyst_control_panel_keyboard(rec_id)
        await query.edit_message_text(text=new_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else: await query.answer("Action failed. The recommendation might be closed or not found.", show_alert=True)
async def start_close_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (updated to use the new state key)
    query = update.callback_query; rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_INPUT_KEY] = {"action": "close", "rec_id": rec_id}
    await query.answer(); await query.edit_message_text(text=f"{query.message.text}\n\n<b>🔻 Please reply with the exit price for #{rec_id}.</b>", parse_mode=ParseMode.HTML)
async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (updated to use the new state key)
    query = update.callback_query; _, _, rec_id_str, exit_price_str = query.data.split(':'); rec_id = int(rec_id_str); exit_price = float(exit_price_str)
    await query.answer("Closing recommendation..."); trade_service: TradeService = get_service(context, "trade_service")
    try:
        rec = trade_service.close(rec_id, exit_price); final_text = "✅ Recommendation closed successfully.\n\n" + build_trade_card_text(rec)
        await query.edit_message_text(text=final_text, parse_mode=ParseMode.HTML)
    except Exception as e: await query.edit_message_text(f"❌ Failed to close recommendation: {e}")
    finally:
        if context.user_data.get(AWAITING_INPUT_KEY, {}).get("rec_id") == rec_id: context.user_data.pop(AWAITING_INPUT_KEY, None)
async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (updated to use the new state key and restore panel)
    query = update.callback_query; rec_id = int(query.data.split(':')[2])
    if context.user_data.get(AWAITING_INPUT_KEY, {}).get("rec_id") == rec_id: context.user_data.pop(AWAITING_INPUT_KEY, None)
    trade_service: TradeService = get_service(context, "trade_service"); rec = trade_service.repo.get(rec_id)
    text = "Operation cancelled. Control panel:\n\n" + build_trade_card_text(rec); keyboard = analyst_control_panel_keyboard(rec_id)
    await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

# --- Edit Menu Handlers ---
async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (unchanged)
    query = update.callback_query; rec_id = int(query.data.split(':')[2]); keyboard = analyst_edit_menu_keyboard(rec_id)
    await query.answer(); await query.edit_message_reply_markup(reply_markup=keyboard)
async def back_to_main_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (unchanged)
    query = update.callback_query; rec_id = int(query.data.split(':')[2]); keyboard = analyst_control_panel_keyboard(rec_id)
    await query.answer(); await query.edit_message_reply_markup(reply_markup=keyboard)

# ✅ --- NEW HANDLERS FOR EDITING SL AND TP ---
async def start_edit_sl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_sl", "rec_id": rec_id}
    await query.answer(); await query.edit_message_text(text=f"{query.message.text}\n\n<b>✏️ Please reply with the new Stop Loss value for #{rec_id}.</b>", parse_mode=ParseMode.HTML)
async def start_edit_tp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_tp", "rec_id": rec_id}
    await query.answer(); await query.edit_message_text(text=f"{query.message.text}\n\n<b>🎯 Please reply with the new targets for #{rec_id} (separated by space).</b>", parse_mode=ParseMode.HTML)

# --- Universal Message Handler for Awaiting Input ---
async def received_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_INPUT_KEY not in context.user_data: return
    
    state = context.user_data[AWAITING_INPUT_KEY]
    action = state["action"]
    rec_id = state["rec_id"]
    user_input = update.message.text.strip()
    trade_service: TradeService = get_service(context, "trade_service")

    try:
        if action == "close":
            exit_price = float(user_input); text = f"Confirm closing <b>#{rec_id}</b> at <code>{exit_price}</code>?"; keyboard = confirm_close_keyboard(rec_id, exit_price)
            await update.message.reply_html(text, reply_markup=keyboard)
        
        elif action == "edit_sl":
            new_sl = float(user_input); trade_service.update_sl(rec_id, new_sl)
            await update.message.reply_text("✅ Stop Loss updated successfully! A confirmation message has been sent.", reply_markup=None)
        
        elif action == "edit_tp":
            new_targets = [float(t) for t in user_input.replace(",", " ").split()];
            if not new_targets: raise ValueError("No targets provided.")
            trade_service.update_targets(rec_id, new_targets)
            await update.message.reply_text("✅ Targets updated successfully! A confirmation message has been sent.", reply_markup=None)

    except ValueError as e:
        await update.message.reply_text(f"⚠️ Invalid input: {e}. Please try again.")
    except Exception as e:
        log.error(f"Error processing awaited input for action {action}, rec_id {rec_id}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ An error occurred: {e}")
    finally:
        # Clear state after processing
        context.user_data.pop(AWAITING_INPUT_KEY, None)

def register_management_handlers(application: Application):
    # Public
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:"))
    # Analyst Panel
    application.add_handler(CallbackQueryHandler(update_private_card, pattern=r"^rec:update_private:"))
    application.add_handler(CallbackQueryHandler(move_sl_to_be_handler, pattern=r"^rec:move_be:"))
    application.add_handler(CallbackQueryHandler(partial_close_note_handler, pattern=r"^rec:close_partial:"))
    application.add_handler(CallbackQueryHandler(start_close_flow_handler, pattern=r"^rec:close_start:"))
    # Edit Menu
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:"))
    application.add_handler(CallbackQueryHandler(back_to_main_panel_handler, pattern=r"^rec:back_to_main:"))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:"))
    application.add_handler(CallbackQueryHandler(start_edit_tp_handler, pattern=r"^rec:edit_tp:"))
    # Closing Flow
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:"))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:"))
    # Universal Input Handler (must have a higher group number to run after commands)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_input_handler), group=1)
# --- END OF FILE ---