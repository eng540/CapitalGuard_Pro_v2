# src/capitalguard/interfaces/telegram/management_handlers.py (v27.4 - Final & Complete)
"""
Implements all callback query handlers for managing recommendations and trades.
This is the final, complete, and fully implemented version.
"""

import logging
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler, CommandHandler)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_tail_int, parse_cq_parts
from .keyboards import (analyst_control_panel_keyboard, build_open_recs_keyboard, build_user_trade_control_keyboard, build_close_options_keyboard, analyst_edit_menu_keyboard, build_exit_strategy_keyboard, build_partial_close_keyboard, confirm_close_keyboard)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)

# --- Helper Functions ---

async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    parts = parse_cq_parts(query.data)
    position_type, position_id = parts[2], int(parts[3])
    
    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), position_type, position_id)
    
    if not position:
        await query.edit_message_text("❌ Position not found or access denied.")
        return

    price_service = get_service(context, "price_service", PriceService)
    live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
    if live_price: setattr(position, "live_price", live_price)

    text = build_trade_card_text(position)
    is_trade = getattr(position, 'is_user_trade', False)
    
    keyboard = None
    if is_trade:
        keyboard = build_user_trade_control_keyboard(position_id)
    elif position.status != RecommendationStatus.CLOSED:
        keyboard = analyst_control_panel_keyboard(position)

    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" not in str(e): log.warning(f"Failed to edit panel message: {e}")

async def _prompt_for_input(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, action: str, prompt_text: str):
    rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_INPUT_KEY] = {"action": action, "rec_id": rec_id, "original_message": query.message}
    full_prompt = f"{query.message.text}\n\n<b>{prompt_text}</b>"
    await query.edit_message_text(full_prompt, parse_mode=ParseMode.HTML)

# --- Main Panel & Navigation ---

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    await update.callback_query.answer()
    await _send_or_edit_position_panel(update, context, db_session)

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    page = parse_tail_int(query.data) or 1
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    items = trade_service.get_open_positions_for_user(db_session, str(query.from_user.id))
    if not items:
        await query.edit_message_text(text="✅ You have no open positions.")
        return
    keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
    await query.edit_message_text(text="<b>📊 Your Open Positions</b>\nSelect one to manage:", reply_markup=keyboard, parse_mode=ParseMode.HTML)

# --- Menu Navigation ---

@uow_transaction
@require_active_user
@require_analyst_user
async def show_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    parts = parse_cq_parts(query.data)
    action, rec_id = parts[1], int(parts[2])

    if action == "edit_menu":
        await query.edit_message_reply_markup(reply_markup=analyst_edit_menu_keyboard(rec_id))
    elif action == "close_menu":
        await query.edit_message_reply_markup(reply_markup=build_close_options_keyboard(rec_id))
    elif action == "strategy_menu":
        trade_service = get_service(context, "trade_service", TradeService)
        rec_orm = trade_service.repo.get(db_session, rec_id)
        if rec_orm:
            rec_entity = trade_service.repo._to_entity(rec_orm)
            await query.edit_message_reply_markup(reply_markup=build_exit_strategy_keyboard(rec_entity))
    elif action == "close_partial":
        await query.edit_message_reply_markup(reply_markup=build_partial_close_keyboard(rec_id))

# --- Direct Actions ---

@uow_transaction
@require_active_user
@require_analyst_user
async def set_strategy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer("Updating strategy...")
    parts = parse_cq_parts(query.data)
    rec_id, strategy_value = int(parts[2]), parts[3]
    trade_service = get_service(context, "trade_service", TradeService)
    await trade_service.update_exit_strategy_async(rec_id, str(query.from_user.id), ExitStrategy(strategy_value), db_session)

@uow_transaction
@require_active_user
@require_analyst_user
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer("Fetching market price & closing...")
    rec_id = int(query.data.split(':')[2])
    trade_service = get_service(context, "trade_service", TradeService)
    rec_orm = trade_service.repo.get(db_session, rec_id)
    if not rec_orm: return
    rec_entity = trade_service.repo._to_entity(rec_orm)
    
    price_service = get_service(context, "price_service", PriceService)
    live_price = await price_service.get_cached_price(rec_entity.asset.value, rec_entity.market, force_refresh=True)
    if live_price is None:
        await query.edit_message_text(f"❌ Could not fetch market price for {rec_entity.asset.value}.")
        return
    await trade_service.close_recommendation_async(rec_id, str(query.from_user.id), Decimal(str(live_price)), db_session)

# --- Input Prompts & Handlers ---

async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(':')[1]
    prompts = {
        "edit_sl": "✏️ Reply to this message with the new Stop Loss.",
        "edit_tp": "🎯 Reply with the new list of targets (e.g., 50k 52k@50).",
        "close_manual": "✍️ Reply with the final closing price."
    }
    await _prompt_for_input(query, context, action, prompts.get(action, "Please reply with the new value."))

@uow_transaction
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    if not context.user_data or not (state := context.user_data.pop(AWAITING_INPUT_KEY, None)): return
    orig_msg = state.get("original_message")
    if not orig_msg or not update.message.reply_to_message or update.message.reply_to_message.message_id != orig_msg.message_id:
        if state: context.user_data[AWAITING_INPUT_KEY] = state
        return

    action, rec_id = state["action"], state["rec_id"]
    user_input, chat_id, user_id = update.message.text.strip(), orig_msg.chat_id, str(update.effective_user.id)
    
    try: await update.message.delete()
    except Exception: pass

    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        if action == "close_manual":
            price = parse_number(user_input)
            if price is None: raise ValueError("Invalid price format.")
            await trade_service.close_recommendation_async(rec_id, user_id, price, db_session=db_session)
        elif action == "edit_sl":
            price = parse_number(user_input)
            if price is None: raise ValueError("Invalid price format.")
            await trade_service.update_sl_for_user_async(rec_id, user_id, price, db_session=db_session)
        elif action == "edit_tp":
            targets_list = parse_targets_list(user_input.split())
            if not targets_list: raise ValueError("Invalid targets format.")
            await trade_service.update_targets_for_user_async(rec_id, user_id, targets_list, db_session=db_session)
    except Exception as e:
        log.error(f"Error processing reply for {action} on #{rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")

# --- Registration ---

def register_management_handlers(app: Application):
    # Main navigation
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=r"^open_nav:page:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^(pos:show_panel:|rec:back_to_main:)"))
    
    # Menu navigation
    app.add_handler(CallbackQueryHandler(show_menu_handler, pattern=r"^rec:(edit_menu|close_menu|strategy_menu|close_partial)"))

    # Direct actions
    app.add_handler(CallbackQueryHandler(set_strategy_handler, pattern=r"^rec:set_strategy:"))
    app.add_handler(CallbackQueryHandler(close_at_market_handler, pattern=r"^rec:close_market:"))

    # Actions that prompt for user input
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=r"^rec:(edit_sl|edit_tp|close_manual)"))
    
    # Handler for text replies to prompts
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, reply_handler))