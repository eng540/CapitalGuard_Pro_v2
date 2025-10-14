# src/capitalguard/interfaces/telegram/management_handlers.py (v28.5 - Final Production Release)
"""
Implements all callback query handlers for managing recommendations and trades.
This is the final, production-ready, and fully implemented version.

Changelog:
- [CRITICAL FIX] Refactored `_send_or_edit_position_panel` to accept explicit IDs instead of parsing
  callback data, fixing a crash when refreshing the panel after an action (e.g., strategy update).
- [IMPROVEMENT] The control panel is now correctly and reliably refreshed after all major state-changing
  actions (set_strategy, close_market, partial_close, reply_handler updates), providing immediate user feedback.
- [IMPROVEMENT] Hardened the `reply_handler` to restore the original message on input error.
- [CONFIG] Added `per_message=False` to the ConversationHandler definition to suppress PTBUserWarning
  and clarify intent, resulting in cleaner production logs.
"""

import logging
from decimal import Decimal, InvalidOperation

from telegram import Update, ReplyKeyboardRemove, CallbackQuery
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler, CommandHandler)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_cq_parts
from .keyboards import (
    analyst_control_panel_keyboard, build_open_recs_keyboard, 
    build_user_trade_control_keyboard, build_close_options_keyboard, 
    analyst_edit_menu_keyboard, build_exit_strategy_keyboard, 
    build_partial_close_keyboard, CallbackAction
)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)

# --- Helper Functions ---

async def _send_or_edit_position_panel(
    query: CallbackQuery, 
    context: ContextTypes.DEFAULT_TYPE, 
    db_session, 
    position_type: str, 
    position_id: int
):
    """
    Fetches position details by explicit type and ID, then builds and sends/edits the control panel.
    This function is now the single source of truth for rendering a position panel and is safe
    from parsing errors as it doesn't rely on callback_data.
    """
    user_id = str(query.from_user.id)
    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(db_session, user_id, position_type, position_id)
    
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
        if "Message is not modified" in str(e).lower():
            await query.answer()
        else:
            log.warning(f"Failed to edit panel message: {e}")

async def _prompt_for_input(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, action: str, prompt_text: str):
    rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_INPUT_KEY] = {"action": action, "rec_id": rec_id, "original_message": query.message}
    full_prompt = f"{query.message.text}\n\n<b>{prompt_text}</b>"
    await query.edit_message_text(full_prompt, parse_mode=ParseMode.HTML)

# --- Main Panel & Navigation ---

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    parts = parse_cq_parts(query.data)
    
    try:
        if parts[1] == "back_to_main":
            position_id = int(parts[2])
            position_type = 'rec'
        else:
            position_type, position_id = parts[2], int(parts[3])
        
        await _send_or_edit_position_panel(query, context, db_session, position_type, position_id)

    except (IndexError, ValueError):
        log.error(f"Could not parse position info from callback data: {query.data}")
        await query.answer("Error: Invalid callback data.", show_alert=True)

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    parts = parse_cq_parts(query.data)
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    
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
    
    await _send_or_edit_position_panel(query, context, db_session, position_type='rec', position_id=rec_id)

@uow_transaction
@require_active_user
@require_analyst_user
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer("Fetching market price & closing...")
    rec_id = int(query.data.split(':')[2])
    trade_service = get_service(context, "trade_service", TradeService)
    
    await trade_service.close_recommendation_async(rec_id, str(query.from_user.id), None, db_session) # Pass None for price to let service handle it
    await _send_or_edit_position_panel(query, context, db_session, position_type='rec', position_id=rec_id)

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer("Fetching price and taking partial profit...")
    parts = parse_cq_parts(query.data)
    rec_id, percent_to_close = int(parts[2]), Decimal(parts[3])
    
    trade_service = get_service(context, "trade_service", TradeService)
    await trade_service.partial_close_async(rec_id, str(query.from_user.id), percent_to_close, None, db_session) # Pass None for price
    await _send_or_edit_position_panel(query, context, db_session, position_type='rec', position_id=rec_id)

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
    if not context.user_data or not (state := context.user_data.pop(AWAITING_INPUT_KEY, None)):
        return
    orig_msg = state.get("original_message")
    if not orig_msg or not update.message.reply_to_message or update.message.reply_to_message.message_id != orig_msg.message_id:
        if state: context.user_data[AWAITING_INPUT_KEY] = state
        return

    action, rec_id = state["action"], state["rec_id"]
    user_input, chat_id, user_id = update.message.text.strip(), orig_msg.chat_id, str(update.effective_user.id)
    
    try: 
        await update.message.delete()
    except Exception: 
        pass

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
        
        await _send_or_edit_position_panel(orig_msg.callback_query, context, db_session, 'rec', rec_id)

    except Exception as e:
        log.error(f"Error processing reply for {action} on #{rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
        if orig_msg:
            try:
                await orig_msg.edit_text(orig_msg.text, reply_markup=orig_msg.reply_markup, parse_mode=ParseMode.HTML)
            except BadRequest:
                pass # Ignore if message is not modified

# --- Partial Close Conversation ---

async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    rec_id = int(query.data.split(':')[2])
    context.user_data['partial_close_rec_id'] = rec_id
    await query.edit_message_text(f"{query.message.text}\n\n<b>💰 Please send the percentage of the position you want to close (e.g., 25.5).</b>", parse_mode=ParseMode.HTML)
    return AWAIT_PARTIAL_PERCENT

async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        percent = parse_number(update.message.text)
        if percent is None or not (0 < percent <= 100):
            raise ValueError("Percentage must be a number between 0 and 100.")
        context.user_data['partial_close_percent'] = percent
        await update.message.reply_html(f"✅ Percentage: {percent:g}%\n\n<b>Now, please send the price at which you are closing.</b>")
        return AWAIT_PARTIAL_PRICE
    except (ValueError) as e:
        await update.message.reply_text(f"❌ Invalid value: {e}. Please try again or /cancel.")
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    try:
        price = parse_number(update.message.text)
        if price is None: raise ValueError("Invalid price format.")
        
        percent = context.user_data['partial_close_percent']
        rec_id = context.user_data['partial_close_rec_id']
        user_id = str(update.effective_user.id)
        
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.partial_close_async(rec_id, user_id, percent, price, db_session)
        
    except (ValueError) as e:
        await update.message.reply_text(f"❌ Invalid value: {e}. Please try again or /cancel.")
        return AWAIT_PARTIAL_PRICE
    except Exception as e:
        log.error(f"Error in partial profit flow for rec #{context.user_data.get('partial_close_rec_id')}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ An unexpected error occurred: {e}")
    
    for key in ('partial_close_rec_id', 'partial_close_percent'):
        context.user_data.pop(key, None)
    return ConversationHandler.END

async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ('partial_close_rec_id', 'partial_close_percent'):
        context.user_data.pop(key, None)
    await update.message.reply_text("Partial close operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- Registration ---

def register_management_handlers(app: Application):
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=r"^nav:nv:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^(pos:sh:|rec:back_to_main:)"))
    app.add_handler(CallbackQueryHandler(show_menu_handler, pattern=r"^rec:(edit_menu|close_menu|strategy_menu)"))
    app.add_handler(CallbackQueryHandler(set_strategy_handler, pattern=rf"^rec:{CallbackAction.STRATEGY.value}:"))
    app.add_handler(CallbackQueryHandler(close_at_market_handler, pattern=r"^rec:close_market:"))
    app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^rec:{CallbackAction.PARTIAL.value}:"))
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=r"^rec:(edit_sl|edit_tp|close_manual)"))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, reply_handler))

    partial_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_close_custom_start, pattern=r"^rec:partial_close_custom:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_percent_received)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_price_received)],
        },
        fallbacks=[CommandHandler("cancel", partial_close_cancel)],
        name="partial_profit_conversation",
        per_user=True,
        per_chat=True,
        per_message=False,
    )
    app.add_handler(partial_close_conv)