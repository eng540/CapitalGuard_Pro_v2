# src/capitalguard/interfaces/telegram/management_handlers.py (v25.7 - FINAL & STATE-SAFE)
"""
Implements all callback query handlers for managing existing recommendations and trades.
This version ensures correct decorator order and state-safe database access.
"""

import logging
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_tail_int, parse_cq_parts
from .keyboards import (
    analyst_control_panel_keyboard,
    build_open_recs_keyboard,
    build_user_trade_control_keyboard,
    build_close_options_keyboard,
    confirm_close_keyboard,
    analyst_edit_menu_keyboard
)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"

async def _send_or_edit_position_panel(
    context: ContextTypes.DEFAULT_TYPE, 
    db_session, 
    chat_id: int, 
    message_id: int, 
    position_id: int, 
    user_id: int,
    position_type: str
):
    """Unified and robust function to render the management panel for any position type."""
    trade_service = get_service(context, "trade_service", TradeService)
    
    position = trade_service.get_position_details_for_user(db_session, str(user_id), position_type, position_id)
    
    if not position:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Position not found or access denied.")
        return
    
    price_service = get_service(context, "price_service", PriceService)
    live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
    if live_price: 
        setattr(position, "live_price", live_price)
    
    text = build_trade_card_text(position)
    is_trade = getattr(position, 'is_user_trade', False)
    
    if is_trade:
        keyboard = build_user_trade_control_keyboard(position_id)
    else:
        keyboard = analyst_control_panel_keyboard(position) if position.status != RecommendationStatus.CLOSED else None

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, 
            reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"Failed to edit message for position panel: {e}")

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles callbacks to show the main management panel for a position."""
    query = update.callback_query
    await query.answer()
    
    parts = parse_cq_parts(query.data)
    if len(parts) < 4:
        await query.edit_message_text("❌ Invalid request.")
        return

    position_type = parts[2]
    position_id = int(parts[3])
    user_id = query.from_user.id

    await _send_or_edit_position_panel(
        context, db_session, 
        query.message.chat_id, query.message.message_id, 
        position_id, user_id, position_type
    )

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles pagination for the list of open positions."""
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
    await query.edit_message_text(
        text="<b>📊 Your Open Positions</b>\nSelect a position to manage:",
        reply_markup=keyboard, 
        parse_mode=ParseMode.HTML
    )

@uow_transaction
@require_active_user
@require_analyst_user
async def show_close_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
    """Displays the close options menu (Market vs. Manual)."""
    query = update.callback_query
    await query.answer()
    rec_id = parse_tail_int(query.data)
    if not rec_id: return
    
    text = f"{query.message.text}\n\n--- \n<b>Choose closing method:</b>"
    keyboard = build_close_options_keyboard(rec_id)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@uow_transaction
@require_active_user
@require_analyst_user
async def close_with_manual_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
    """Initiates the manual close workflow by asking for a price."""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    if rec_id is None: return
    
    context.user_data[AWAITING_INPUT_KEY] = {
        "action": "close_rec", 
        "rec_id": rec_id, 
        "original_message": query.message
    }
    
    await query.answer()
    await query.edit_message_text(
        f"{query.message.text}\n\n<b>✍️ Please <u>reply to this message</u> with the desired closing price for recommendation #{rec_id}.</b>", 
        parse_mode=ParseMode.HTML
    )

@uow_transaction
@require_active_user
@require_analyst_user
async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
    """Displays the edit menu (SL vs. TP)."""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    if rec_id is None: return
    
    keyboard = analyst_edit_menu_keyboard(rec_id)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=keyboard)

@uow_transaction
@require_active_user
@require_analyst_user
async def start_edit_sl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
    """Initiates the SL edit workflow."""
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    if rec_id is None: return
    
    context.user_data[AWAITING_INPUT_KEY] = {
        "action": "edit_sl", 
        "rec_id": rec_id, 
        "original_message": query.message
    }
    
    await query.answer()
    await query.edit_message_text(
        f"{query.message.text}\n\n<b>✏️ Please <u>reply to this message</u> with the new Stop Loss value for recommendation #{rec_id}.</b>", 
        parse_mode=ParseMode.HTML
    )

@uow_transaction
async def unified_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """A single, robust handler for all text inputs that are replies to bot prompts."""
    if not update.message or not context.user_data: return
    
    state = context.user_data.get(AWAITING_INPUT_KEY)
    if not state or not update.message.reply_to_message: return
    
    original_message = state.get("original_message")
    if not original_message or update.message.reply_to_message.message_id != original_message.message_id:
        return

    action = state["action"]
    position_id = state.get("rec_id") or state.get("trade_id")
    user_input = update.message.text.strip()
    chat_id, message_id, user_id = original_message.chat_id, original_message.message_id, update.effective_user.id
    
    try:
        await update.message.delete()
    except Exception:
        pass

    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        if action == "close_rec":
            exit_price = Decimal(user_input)
            text = f"Confirm closing <b>#{position_id}</b> at <b>{exit_price}</b>?"
            keyboard = confirm_close_keyboard(position_id, float(exit_price))
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, 
                text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
            return

        elif action == "edit_sl":
            new_sl = Decimal(user_input)
            await trade_service.update_sl_for_user_async(position_id, str(user_id), new_sl, db_session=db_session)
            context.user_data.pop(AWAITING_INPUT_KEY, None)
            await _send_or_edit_position_panel(context, db_session, chat_id, message_id, position_id, user_id, 'rec')
            return

    except (InvalidOperation, ValueError) as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Invalid number format: {e}. Please try again.")
    except Exception as e:
        log.error(f"Error processing input for action {action}, ID {position_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ An error occurred: {e}")
        context.user_data.pop(AWAITING_INPUT_KEY, None)
        await _send_or_edit_position_panel(context, db_session, chat_id, message_id, position_id, user_id, 'rec')

def register_management_handlers(application: Application):
    """Registers all callback query and message handlers for managing positions."""
    application.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=r"^open_nav:page:"))
    application.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^pos:show_panel:"))
    application.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^rec:back_to_main:"))
    application.add_handler(CallbackQueryHandler(show_close_menu_handler, pattern=r"^rec:close_menu:"))
    application.add_handler(CallbackQueryHandler(close_with_manual_price_handler, pattern=r"^rec:close_manual:"))
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:"))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:"))
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler), group=1)

#END