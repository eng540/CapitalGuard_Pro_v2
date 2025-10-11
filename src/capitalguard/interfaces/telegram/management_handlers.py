# src/capitalguard/interfaces/telegram/management_handlers.py (v27.3 - Final & Complete)
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

async def _send_or_edit_position_panel(context: ContextTypes.DEFAULT_TYPE, db_session, chat_id: int, message_id: int, position_id: int, user_id: int, position_type: str):
    # ... (unchanged)
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        position = trade_service.get_position_details_for_user(db_session, str(user_id), position_type, position_id)
        if not position:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ **Error:** Position not found or access denied.")
            return

        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
        if live_price: setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)
        is_trade = getattr(position, 'is_user_trade', False)
        
        if is_trade: keyboard = build_user_trade_control_keyboard(position_id)
        else: keyboard = analyst_control_panel_keyboard(position) if position.status != RecommendationStatus.CLOSED else None

        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" not in str(e): log.warning(f"Failed to edit panel message: {e}")
    except Exception as e:
        log.error(f"Critical error in _send_or_edit_position_panel for pos_id={position_id}: {e}", exc_info=True)
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ An unexpected error occurred while loading position details.")

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    # ... (unchanged)
    query = update.callback_query
    await query.answer()
    parts = parse_cq_parts(query.data)
    if len(parts) < 4: return
    position_type, position_id = parts[2], int(parts[3])
    await _send_or_edit_position_panel(context, db_session, query.message.chat_id, query.message.message_id, position_id, query.from_user.id, position_type)

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    # ... (unchanged)
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

async def _prompt_for_input(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, action: str, prompt_text: str):
    # ... (unchanged)
    rec_id = parse_tail_int(query.data)
    if rec_id is None: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": action, "rec_id": rec_id, "original_message": query.message}
    full_prompt = f"{query.message.text}\n\n<b>{prompt_text}</b>"
    await query.edit_message_text(full_prompt, parse_mode=ParseMode.HTML)

@uow_transaction
@require_active_user
@require_analyst_user
async def unified_management_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    parts = parse_cq_parts(query.data)
    action = parts[1]
    rec_id = int(parts[2])
    
    trade_service = get_service(context, "trade_service", TradeService)
    rec_orm = trade_service.repo.get(db_session, rec_id)
    if not rec_orm:
        await query.edit_message_text("❌ Recommendation not found.")
        return
    rec_entity = trade_service.repo._to_entity(rec_orm)

    # --- Routing based on action ---
    if action == "edit_menu":
        await query.edit_message_reply_markup(reply_markup=analyst_edit_menu_keyboard(rec_id))
    elif action == "close_menu":
        await query.edit_message_reply_markup(reply_markup=build_close_options_keyboard(rec_id))
    elif action == "strategy_menu":
        await query.edit_message_reply_markup(reply_markup=build_exit_strategy_keyboard(rec_entity))
    elif action == "close_partial":
        await query.edit_message_reply_markup(reply_markup=build_partial_close_keyboard(rec_id))
    elif action == "set_strategy":
        strategy_value = parts[3]
        await trade_service.update_exit_strategy_async(rec_id, str(query.from_user.id), ExitStrategy(strategy_value), db_session)
    elif action == "edit_sl":
        await _prompt_for_input(query, context, "edit_sl", f"✏️ Reply to this message with the new Stop Loss for #{rec_id}.")
    elif action == "edit_tp":
        await _prompt_for_input(query, context, "edit_tp", f"🎯 Reply with the new list of targets for #{rec_id} (e.g., 50k 52k@50).")
    elif action == "close_manual":
        await _prompt_for_input(query, context, "close_manual", f"✍️ Reply with the final closing price for #{rec_id}.")
    elif action == "close_market":
        await query.edit_message_text("Closing at market price...")
        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(rec_entity.asset.value, rec_entity.market, force_refresh=True)
        if live_price is None:
            await query.edit_message_text(f"❌ Could not fetch market price for {rec_entity.asset.value}.")
            return
        await trade_service.close_recommendation_async(rec_id, str(query.from_user.id), Decimal(str(live_price)), db_session)

@uow_transaction
async def unified_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    # ... (unchanged)
    if not context.user_data: return
    if not (state := context.user_data.pop(AWAITING_INPUT_KEY, None)) or not (orig_msg := state.get("original_message")) or not update.message.reply_to_message or update.message.reply_to_message.message_id != orig_msg.message_id:
        if state: context.user_data[AWAITING_INPUT_KEY] = state
        return
    action, position_id = state["action"], state["rec_id"]
    user_input, chat_id, message_id, user_id = update.message.text.strip(), orig_msg.chat_id, orig_msg.message_id, str(update.effective_user.id)
    try: await update.message.delete()
    except Exception: pass
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        if action == "close_manual":
            price = parse_number(user_input)
            if price is None: raise ValueError("Invalid price format.")
            await trade_service.close_recommendation_async(position_id, user_id, price, db_session=db_session)
        elif action == "edit_sl":
            price = parse_number(user_input)
            if price is None: raise ValueError("Invalid price format.")
            await trade_service.update_sl_for_user_async(position_id, user_id, price, db_session=db_session)
        elif action == "edit_tp":
            targets_list = parse_targets_list(user_input.split())
            if not targets_list: raise ValueError("Invalid targets format.")
            await trade_service.update_targets_for_user_async(position_id, user_id, targets_list, db_session=db_session)
    except (InvalidOperation, ValueError, RuntimeError, TypeError) as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ **Action Failed:** {e}\nPlease try again.", reply_to_message_id=message_id)
        context.user_data[AWAITING_INPUT_KEY] = state
    except Exception as e:
        log.error(f"Critical error processing user input for {action} on #{position_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ An unexpected internal error occurred: {e}", reply_to_message_id=message_id)

def register_management_handlers(app: Application):
    # ✅ THE FIX: Register a single, powerful handler for all `rec:` actions.
    unified_handler = CallbackQueryHandler(unified_management_handler, pattern=r"^rec:")
    
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=r"^open_nav:page:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=r"^(pos:show_panel:|rec:back_to_main:)"))
    
    app.add_handler(unified_handler)
    
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler))