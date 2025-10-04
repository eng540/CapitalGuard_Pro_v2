# src/capitalguard/interfaces/telegram/management_handlers.py (v16.2 - Final Multi-Tenant)
"""
This file contains the callback query handlers for the analyst's control panel.
It has been fully updated to be compatible with the v3.0 multi-tenant architecture
and includes all previous UX hotfixes.
"""

import logging
from time import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CommandHandler,
)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from .helpers import get_service, unit_of_work, parse_tail_int, parse_cq_parts
from .keyboards import (
    analyst_control_panel_keyboard,
    analyst_edit_menu_keyboard,
    confirm_close_keyboard,
    build_open_recs_keyboard,
    build_exit_strategy_keyboard,
    build_close_options_keyboard,
    public_channel_keyboard,
)
from .ui_texts import build_trade_card_text
from .parsers import parse_number, parse_targets_list
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.base import SessionLocal

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)
PUBLIC_UPDATE_COOLDOWN = 15

@unit_of_work
async def _send_or_edit_rec_panel(context: ContextTypes.DEFAULT_TYPE, db_session, chat_id: int, message_id: int, rec_id: int, user_id: int):
    trade_service = get_service(context, "trade_service", TradeService)
    # In v3, this function is primarily for analysts viewing their Recommendations.
    # A similar function will be needed for traders viewing their UserTrades.
    rec_entity = trade_service.get_recommendation_for_user(db_session, rec_id, str(user_id))
    if not rec_entity:
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Recommendation not found or you don't have access.")
        except Exception: pass
        return
        
    price_service = get_service(context, "price_service", PriceService)
    try:
        live_price = await price_service.get_cached_price(rec_entity.asset.value, rec_entity.market, force_refresh=True)
        if live_price: setattr(rec_entity, "live_price", live_price)
    except Exception: pass
    
    text = build_trade_card_text(rec_entity)
    keyboard = analyst_control_panel_keyboard(rec_entity) if rec_entity.status != RecommendationStatus.CLOSED else None
    
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"Failed to edit message for rec panel: {e}")

@unit_of_work
async def _send_or_edit_strategy_menu(context: ContextTypes.DEFAULT_TYPE, db_session, chat_id: int, message_id: int, rec_id: int, user_id: int):
    trade_service = get_service(context, "trade_service", TradeService)
    rec = trade_service.get_recommendation_for_user(db_session, rec_id, str(user_id))
    if not rec:
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Recommendation not found.")
        except Exception: pass
        return

    strategy_text = "Auto-close at final TP" if rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP else "Manual close only"
    profit_stop_text = f"{rec.profit_stop_price:g}" if getattr(rec, "profit_stop_price", None) is not None else "Not set"
    text = (f"<b>Signal #{rec.id} | {rec.asset.value}</b>\n"
            f"------------------------------------\n"
            f"<b>Manage Exit Strategy</b>\n\n"
            f"<b>- Current Close Strategy:</b> {strategy_text}\n"
            f"<b>- Current Profit Stop:</b> {profit_stop_text}\n\n"
            f"Choose an action:")
    keyboard = build_exit_strategy_keyboard(rec)
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"Failed to edit strategy menu for rec #{rec_id}: {e}")

@unit_of_work
async def show_rec_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    await query.answer()
    rec_id = parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_rec_panel(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

@require_active_user
@require_analyst_user
@unit_of_work
async def cancel_pending_rec_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    if not rec_id:
        await query.answer("Invalid request.", show_alert=True)
        return
    await query.answer("Cancelling recommendation...")
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        updated_rec = await trade_service.cancel_pending_recommendation_manual(rec_id, str(query.from_user.id), db_session=db_session)
        await query.edit_message_text(f"✅ Recommendation #{updated_rec.id} ({updated_rec.asset.value}) has been successfully cancelled.")
    except ValueError as e:
        await query.answer(str(e), show_alert=True)
        await _send_or_edit_rec_panel(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

@require_active_user
@require_analyst_user
@unit_of_work
async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    parts = parse_cq_parts(query.data)
    if len(parts) < 4:
        await query.answer("Bad request.", show_alert=True)
        return
    try:
        rec_id, exit_price = int(parts[2]), parse_number(parts[3])
    except (ValueError, IndexError) as e:
        await query.answer(f"Invalid value: {e}", show_alert=True)
        return
    await query.answer("Closing recommendation...")
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        await trade_service.close_recommendation_for_user_async(rec_id, str(query.from_user.id), exit_price, reason="MANUAL_CLOSE", db_session=db_session)
        await query.edit_message_text(f"✅ Recommendation #{rec_id} has been successfully closed.")
    except Exception as e:
        log.error(f"Failed to close recommendation #{rec_id} via confirmation: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Failed to close recommendation #{rec_id}. Error: {e}")
    finally:
        context.user_data.pop(AWAITING_INPUT_KEY, None)

@require_active_user
@require_analyst_user
@unit_of_work
async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    rec_id = parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_rec_panel(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

@require_active_user
@require_analyst_user
async def show_close_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = parse_tail_int(query.data)
    if not rec_id: return
    text = f"{query.message.text}\n\n--- \n<b>اختر طريقة الإغلاق:</b>"
    keyboard = build_close_options_keyboard(rec_id)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@require_active_user
@require_analyst_user
@unit_of_work
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    if not rec_id:
        await query.answer("Invalid request.", show_alert=True)
        return
    await query.answer("Fetching market price & closing...")
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        await trade_service.close_recommendation_at_market_for_user_async(rec_id, str(query.from_user.id))
        await query.edit_message_text(f"✅ Recommendation #{rec_id} has been closed at market price.")
    except Exception as e:
        log.error(f"Failed to close rec #{rec_id} at market: {e}", exc_info=True)
        await query.answer(f"Error: {e}", show_alert=True)

@require_active_user
@require_analyst_user
async def close_with_manual_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = parse_tail_int(query.data)
    if rec_id is None:
        await query.answer("Bad request.", show_alert=True)
        return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "close", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n<b>✍️ يرجى <u>الرد على هذه الرسالة ↩️</u> بسعر الإغلاق المحدد للتوصية #{rec_id}.</b>", parse_mode=ParseMode.HTML)

@unit_of_work
async def unified_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    if not update.message or not context.user_data: return
    state = context.user_data.get(AWAITING_INPUT_KEY)
    if not state: return
    original_message = state.get("original_message")
    if not original_message or not update.message.reply_to_message or update.message.reply_to_message.message_id != original_message.message_id: return
    
    action, rec_id = state["action"], state["rec_id"]
    user_input = update.message.text.strip()
    chat_id, message_id, user_id = original_message.chat_id, original_message.message_id, update.effective_user.id
    user_id_str = str(user_id)
    try:
        try: await update.message.delete()
        except Exception: pass
        
        if action == "close":
            exit_price = parse_number(user_input)
            text = f"Confirm closing <b>#{rec_id}</b> at <b>{exit_price:g}</b>?"
            keyboard = confirm_close_keyboard(rec_id, exit_price)
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            # For other actions, we can process them directly
            trade_service = get_service(context, "trade_service", TradeService)
            if action == "edit_sl":
                new_sl = parse_number(user_input)
                await trade_service.update_sl_for_user_async(rec_id, user_id_str, new_sl, db_session=db_session)
            elif action == "edit_tp":
                new_targets = parse_targets_list(user_input.split())
                await trade_service.update_targets_for_user_async(rec_id, user_id_str, new_targets, db_session=db_session)
            
            context.user_data.pop(AWAITING_INPUT_KEY, None)
            await _send_or_edit_rec_panel(context, db_session, chat_id, message_id, rec_id, user_id)

    except Exception as e:
        log.error(f"Error processing input for action {action}, rec_id {rec_id}: {e}", exc_info=True)
        context.user_data.pop(AWAITING_INPUT_KEY, None)
        try: await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
        except Exception: pass
        await _send_or_edit_rec_panel(context, db_session, chat_id, message_id, rec_id, user_id)

def register_management_handlers(application: Application):
    """Registers all callback query handlers for the bot."""
    
    # Handlers for the main control panel and navigation
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:show_panel:", block=False))
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:back_to_main:", block=False))
    
    # Handler for the new cancel button on PENDING recommendations
    application.add_handler(CallbackQueryHandler(cancel_pending_rec_handler, pattern=r"^rec:cancel_pending:", block=False))

    # Handlers for closing a recommendation
    application.add_handler(CallbackQueryHandler(show_close_menu_handler, pattern=r"^rec:close_menu:", block=False))
    application.add_handler(CallbackQueryHandler(close_at_market_handler, pattern=r"^rec:close_market:", block=False))
    application.add_handler(CallbackQueryHandler(close_with_manual_price_handler, pattern=r"^rec:close_manual:", block=False))
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:", block=False))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:", block=False))

    # A message handler for all replies, which will be routed by the unified_reply_handler
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler), group=1)

    # Other handlers can be added here as they are refactored
    # e.g., for editing SL/TP, strategy, etc.