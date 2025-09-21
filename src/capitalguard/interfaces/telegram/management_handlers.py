# --- START OF FINAL, COMPLETE, AND PUBLIC-UPDATE-FIXED FILE (Version 12.2.0) ---
# src/capitalguard/interfaces/telegram/management_handlers.py

import logging
from time import time
from typing import Optional

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
from .helpers import get_service, unit_of_work
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
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.base import SessionLocal

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)
PUBLIC_UPDATE_COOLDOWN = 15 # Seconds to prevent spamming the update button

# --- View Helper Functions ---

async def _send_or_edit_rec_panel(context: ContextTypes.DEFAULT_TYPE, db_session, chat_id: int, message_id: int, rec_id: int, user_id: int):
    """A reusable function to build and send/edit the analyst control panel."""
    trade_service = get_service(context, "trade_service", TradeService)
    rec = trade_service.get_recommendation_for_user(db_session, rec_id, str(user_id))
    if not rec:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Recommendation not found or you don't have access.")
        return
    price_service = get_service(context, "price_service", PriceService)
    live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
    if live_price: setattr(rec, "live_price", live_price)
    text = build_trade_card_text(rec)
    keyboard = analyst_control_panel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text,
            reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"Failed to edit message for rec panel: {e}")

async def _send_or_edit_strategy_menu(context: ContextTypes.DEFAULT_TYPE, db_session, chat_id: int, message_id: int, rec_id: int, user_id: int):
    """A reusable function to build and send/edit the strategy menu."""
    trade_service = get_service(context, "trade_service", TradeService)
    rec = trade_service.get_recommendation_for_user(db_session, rec_id, str(user_id))
    if not rec:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Recommendation not found.")
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
    await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

# --- Helper Functions ---

def _parse_tail_int(data: str) -> Optional[int]:
    try: return int(data.split(":")[-1])
    except (ValueError, IndexError): return None

def _parse_cq_parts(data: str) -> list[str]:
    return data.split(":")

# --- Main Callback Query Handlers ---

async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    A read-only handler to update a recommendation card in a public channel.
    It does not require user permissions but has a cooldown to prevent spam.
    """
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if not rec_id:
        await query.answer("Invalid recommendation ID.", show_alert=True)
        return

    cooldown_key = f"public_update_cooldown_{query.message.chat_id}_{query.message.message_id}"
    last_update_time = context.bot_data.get(cooldown_key, 0)
    if time() - last_update_time < PUBLIC_UPDATE_COOLDOWN:
        await query.answer(f"Please wait {PUBLIC_UPDATE_COOLDOWN} seconds before updating again.", show_alert=True)
        return
    
    await query.answer("Fetching live price...")
    context.bot_data[cooldown_key] = time()

    try:
        with SessionLocal() as session:
            trade_service = get_service(context, "trade_service", TradeService)
            price_service = get_service(context, "price_service", PriceService)
            
            rec = trade_service.repo.get(session, rec_id)
            if not rec:
                await query.edit_message_text("This recommendation is no longer available.")
                return

            live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
            if live_price:
                setattr(rec, "live_price", live_price)

            new_text = build_trade_card_text(rec)
            bot_username = context.bot.username if context.bot else None
            keyboard = public_channel_keyboard(rec.id, bot_username) if rec.status != RecommendationStatus.CLOSED else None
            
            await query.edit_message_text(
                text=new_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"Failed to edit public card for rec #{rec_id}: {e}")
            await query.answer("Could not update the card at this time.", show_alert=True)
    except Exception as e:
        log.error(f"Critical error in update_public_card for rec #{rec_id}: {e}", exc_info=True)
        await query.answer("An internal error occurred.", show_alert=True)

@unit_of_work
async def navigate_open_recs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    await query.answer()
    page = _parse_tail_int(query.data) or 1
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    filters_map = context.user_data.get("last_open_filters", {}) or {}
    items = trade_service.get_open_recommendations_for_user(db_session, str(query.from_user.id), **filters_map)
    if not items:
        await query.edit_message_text(text="✅ No open recommendations match the current filter.")
        return
    keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
    header_text = "<b>📊 Your Open Recommendations Dashboard</b>"
    if filters_map:
        filter_text_parts = [f"{k.capitalize()}: {str(v).upper()}" for k, v in filters_map.items()]
        header_text += f"\n<i>Filtered by: {', '.join(filter_text_parts)}</i>"
    await query.edit_message_text(
        text=f"{header_text}\nSelect a recommendation to view its control panel:", 
        reply_markup=keyboard, parse_mode=ParseMode.HTML
    )

@unit_of_work
async def show_rec_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_rec_panel(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

@unit_of_work
async def strategy_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_strategy_menu(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

@unit_of_work
async def update_private_card(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    await query.answer("Updating price...")
    rec_id = _parse_tail_int(query.data)
    if not rec_id: return
    trade_service = get_service(context, "trade_service", TradeService)
    await trade_service.update_price_tracking_async(db_session, rec_id, str(query.from_user.id))
    await _send_or_edit_rec_panel(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

@unit_of_work
async def unified_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    if not update.message or not context.user_data: return
    state = context.user_data.get(AWAITING_INPUT_KEY)
    if not state: return
    original_message = state.get("original_message")
    if not original_message or not update.message.reply_to_message or update.message.reply_to_message.message_id != original_message.message_id:
        return
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    action, rec_id = state["action"], state["rec_id"]
    user_input = update.message.text.strip()
    chat_id, message_id, user_id = original_message.chat_id, original_message.message_id, update.effective_user.id
    user_id_str = str(user_id)
    try: await update.message.delete()
    except Exception: pass
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        if action == "profit_stop":
            price = parse_number(user_input)
            await trade_service.update_profit_stop_for_user_async(db_session, rec_id, user_id_str, price)
            await _send_or_edit_strategy_menu(context, db_session, chat_id, message_id, rec_id, user_id)
        elif action == "close":
            exit_price = parse_number(user_input)
            text = f"Confirm closing <b>#{rec_id}</b> at <b>{exit_price:g}</b>?"
            keyboard = confirm_close_keyboard(rec_id, exit_price)
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        elif action == "edit_sl":
            new_sl = parse_number(user_input)
            await trade_service.update_sl_for_user_async(db_session, rec_id, user_id_str, new_sl)
            await _send_or_edit_rec_panel(context, db_session, chat_id, message_id, rec_id, user_id)
        elif action == "edit_tp":
            new_targets = parse_targets_list(user_input.split())
            await trade_service.update_targets_for_user_async(db_session, rec_id, user_id_str, new_targets)
            await _send_or_edit_rec_panel(context, db_session, chat_id, message_id, rec_id, user_id)
    except Exception as e:
        log.error(f"Error processing input for action {action}, rec_id {rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
        await _send_or_edit_rec_panel(context, db_session, chat_id, message_id, rec_id, user_id)

@unit_of_work
async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    parts = _parse_cq_parts(query.data)
    if len(parts) < 4: await query.answer("Bad request.", show_alert=True); return
    try: rec_id, exit_price = int(parts[2]), parse_number(parts[3])
    except (ValueError, IndexError) as e: await query.answer(f"Invalid value: {e}", show_alert=True); return
    await query.answer("Closing recommendation...")
    trade_service = get_service(context, "trade_service", TradeService)
    await trade_service.close_recommendation_for_user_async(db_session, rec_id, str(query.from_user.id), exit_price)
    context.user_data.pop(AWAITING_INPUT_KEY, None)

@unit_of_work
async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    rec_id = _parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_rec_panel(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    keyboard = analyst_edit_menu_keyboard(rec_id)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=keyboard)

async def start_edit_sl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_sl", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n<b>✏️ Please <u>reply to this message ↩️</u> with the new Stop Loss value for recommendation #{rec_id}.</b>", parse_mode=ParseMode.HTML)

async def start_edit_tp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_tp", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n<b>🎯 Please <u>reply to this message ↩️</u> with the new targets for recommendation #{rec_id} (space-separated).</b>", parse_mode=ParseMode.HTML)

async def start_profit_stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = _parse_cq_parts(query.data)
    try: rec_id = int(parts[2])
    except Exception: await query.answer("Invalid request.", show_alert=True); return
    if len(parts) > 3 and parts[3] == "remove":
        await _remove_profit_stop_handler(update, context)
        return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "profit_stop", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n<b>🛡️ Please <u>reply to this message ↩️</u> with the new Profit Stop price.</b>", parse_mode=ParseMode.HTML)

@unit_of_work
async def _remove_profit_stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if not rec_id: return
    await query.answer("Removing Profit Stop...")
    trade_service = get_service(context, "trade_service", TradeService)
    await trade_service.update_profit_stop_for_user_async(db_session, rec_id, str(query.from_user.id), None)
    await _send_or_edit_strategy_menu(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

@unit_of_work
async def set_strategy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    await query.answer("Changing strategy...")
    parts = _parse_cq_parts(query.data)
    try: rec_id, strategy_value = int(parts[2]), parts[3]
    except Exception: await query.answer("Invalid request.", show_alert=True); return
    trade_service = get_service(context, "trade_service", TradeService)
    await trade_service.update_exit_strategy_for_user_async(db_session, rec_id, str(query.from_user.id), ExitStrategy(strategy_value))
    await _send_or_edit_strategy_menu(context, db_session, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

async def show_close_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    if not rec_id: return
    text = f"{query.message.text}\n\n--- \n<b>اختر طريقة الإغلاق:</b>"
    keyboard = build_close_options_keyboard(rec_id)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@unit_of_work
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if not rec_id: await query.answer("Invalid request.", show_alert=True); return
    await query.answer("Fetching market price & closing...")
    trade_service = get_service(context, "trade_service", TradeService)
    await trade_service.close_recommendation_at_market_for_user_async(db_session, rec_id, str(query.from_user.id))

async def close_with_manual_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: await query.answer("Bad request.", show_alert=True); return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "close", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n<b>✍️ يرجى <u>الرد على هذه الرسالة ↩️</u> بسعر الإغلاق المحدد للتوصية #{rec_id}.</b>", parse_mode=ParseMode.HTML)

# --- Partial Profit Conversation ---
async def partial_profit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return ConversationHandler.END
    context.user_data['partial_profit_rec_id'] = rec_id
    context.user_data['original_message'] = query.message
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n<b>💰 Please reply with the percentage of the position you want to close (e.g., 50).</b>", parse_mode=ParseMode.HTML)
    return AWAIT_PARTIAL_PERCENT

async def received_partial_percent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        percentage = parse_number(update.message.text)
        if not (0 < percentage <= 100): raise ValueError("Percentage must be between 1 and 100.")
        context.user_data['partial_profit_percent'] = percentage
        await update.message.reply_text(f"✅ Percentage: {percentage}%. Now, please send the price at which you took profit.")
        return AWAIT_PARTIAL_PRICE
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ Invalid value: {e}. Please send a number.")
        return AWAIT_PARTIAL_PERCENT

@unit_of_work
async def received_partial_price(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session) -> int:
    original_message = context.user_data.get('original_message')
    rec_id = context.user_data.get('partial_profit_rec_id')
    user_id = update.effective_user.id
    try:
        price = parse_number(update.message.text)
        percentage = context.user_data['partial_profit_percent']
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.take_partial_profit_for_user_async(db_session, rec_id, str(user_id), percentage, price)
        await update.message.reply_text("✅ Partial profit was successfully registered.")
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ Invalid value: {e}. Please send a valid price.")
        return AWAIT_PARTIAL_PRICE
    except Exception as e:
        log.error(f"Error in partial profit flow: {e}", exc_info=True)
        await update.message.reply_text(f"❌ An error occurred: {e}")
    finally:
        if original_message and rec_id:
            await _send_or_edit_rec_panel(context, db_session, original_message.chat_id, original_message.message_id, rec_id, user_id)
        for key in ('partial_profit_rec_id', 'partial_profit_percent', 'original_message'):
            context.user_data.pop(key, None)
    return ConversationHandler.END

async def cancel_partial_profit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    original_message = context.user_data.get('original_message')
    rec_id = context.user_data.get('partial_profit_rec_id')
    for key in ('partial_profit_rec_id', 'partial_profit_percent', 'original_message'):
        context.user_data.pop(key, None)
    await update.message.reply_text("Partial profit operation cancelled.")
    if original_message and rec_id:
        from capitalguard.infrastructure.db.base import SessionLocal
        with SessionLocal() as db_session:
            await _send_or_edit_rec_panel(context, db_session, original_message.chat_id, original_message.message_id, rec_id, update.effective_user.id)
    return ConversationHandler.END

def register_management_handlers(application: Application):
    application.add_handler(CallbackQueryHandler(navigate_open_recs_handler, pattern=r"^open_nav:page:", block=False))
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:show_panel:", block=False))
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:back_to_main:", block=False))
    application.add_handler(CallbackQueryHandler(strategy_menu_handler, pattern=r"^rec:strategy_menu:", block=False))
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:", block=False))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:", block=False))
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:", block=False))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:", block=False))
    application.add_handler(CallbackQueryHandler(start_edit_tp_handler, pattern=r"^rec:edit_tp:", block=False))
    application.add_handler(CallbackQueryHandler(start_profit_stop_handler, pattern=r"^rec:set_profit_stop:", block=False))
    application.add_handler(CallbackQueryHandler(set_strategy_handler, pattern=r"^rec:set_strategy:", block=False))
    application.add_handler(CallbackQueryHandler(update_private_card, pattern=r"^rec:update_private:", block=False))
    application.add_handler(CallbackQueryHandler(show_close_menu_handler, pattern=r"^rec:close_menu:", block=False))
    application.add_handler(CallbackQueryHandler(close_at_market_handler, pattern=r"^rec:close_market:", block=False))
    application.add_handler(CallbackQueryHandler(close_with_manual_price_handler, pattern=r"^rec:close_manual:", block=False))
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:", block=False))

    partial_profit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_profit_start, pattern=r"^rec:close_partial:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_percent)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel_partial_profit)],
        name="partial_profit_conversation",
        per_user=True, per_chat=True,
    )
    application.add_handler(partial_profit_conv)
    
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler), group=1)

# --- END OF FINAL, COMPLETE, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---