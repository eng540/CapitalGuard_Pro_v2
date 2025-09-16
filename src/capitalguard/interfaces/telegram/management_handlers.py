# --- START OF FINAL, FULLY RE-ARCHITECTED AND ROBUST FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
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
    CommandHandler,
)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from .helpers import get_service
from .keyboards import (
    analyst_control_panel_keyboard,
    analyst_edit_menu_keyboard,
    confirm_close_keyboard,
    build_open_recs_keyboard,
    build_exit_strategy_keyboard,
    public_channel_keyboard,
)
from .ui_texts import build_trade_card_text
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.base import SessionLocal

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"

# --- View Helper Functions (New Refactored Logic) ---

async def _send_or_edit_rec_panel(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, rec_id: int, user_id: int):
    """A reusable function to build and send/edit the analyst control panel."""
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    
    with SessionLocal() as session:
        rec = trade_service.repo.get_by_id_for_user(session, rec_id, user_id)
        if not rec:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Recommendation not found or you don't have access.")
            return

        live_price = await price_service.get_cached_price(rec.asset.value, rec.market)
        if live_price: setattr(rec, "live_price", live_price)
        
        text = build_trade_card_text(rec)
        keyboard = analyst_control_panel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
        
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                log.warning(f"Failed to edit message for rec panel: {e}")

async def _send_or_edit_strategy_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, rec_id: int, user_id: int):
    """A reusable function to build and send/edit the strategy menu."""
    trade_service: TradeService = get_service(context, "trade_service")
    with SessionLocal() as session:
        rec = trade_service.repo.get_by_id_for_user(session, rec_id, user_id)
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

# --- Callback Query Handlers (Now act as simple wrappers) ---

async def show_rec_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_rec_panel(context, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

async def strategy_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_strategy_menu(context, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

# --- Unified Reply Handler (The Core of the Fix) ---

async def unified_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not context.user_data:
        return

    state = context.user_data.get(AWAITING_INPUT_KEY)
    if not state:
        return

    original_message = state.get("original_message")
    if not original_message or not update.message.reply_to_message or update.message.reply_to_message.message_id != original_message.message_id:
        return

    context.user_data.pop(AWAITING_INPUT_KEY, None)
    
    action, rec_id = state["action"], state["rec_id"]
    user_input = update.message.text.strip()
    chat_id = original_message.chat_id
    message_id = original_message.message_id
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    trade_service: TradeService = get_service(context, "trade_service")
    
    try:
        if action == "profit_stop":
            price = parse_number(user_input)
            trade_service.update_profit_stop(rec_id, price)
            await _send_or_edit_strategy_menu(context, chat_id, message_id, rec_id, user_id)
        elif action == "close":
            exit_price = parse_number(user_input)
            text = f"Confirm closing <b>#{rec_id}</b> at <b>{exit_price:g}</b>?"
            keyboard = confirm_close_keyboard(rec_id, exit_price)
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        elif action == "edit_sl":
            new_sl = parse_number(user_input)
            trade_service.update_sl(rec_id, new_sl)
            await _send_or_edit_rec_panel(context, chat_id, message_id, rec_id, user_id)
        elif action == "edit_tp":
            new_targets = parse_targets_list(user_input.split())
            trade_service.update_targets(rec_id, new_targets)
            await _send_or_edit_rec_panel(context, chat_id, message_id, rec_id, user_id)
    except Exception as e:
        log.error(f"Error processing input for action {action}, rec_id {rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
        await _send_or_edit_rec_panel(context, chat_id, message_id, rec_id, user_id)

# --- Other handlers (included for completeness) ---

def _parse_tail_int(data: str) -> Optional[int]:
    try:
        return int(data.split(":")[-1])
    except (ValueError, IndexError):
        return None

def _parse_cq_parts(data: str) -> list[str]:
    return data.split(":")

async def navigate_open_recs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = _parse_tail_int(query.data) or 1
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    filters_map = context.user_data.get("last_open_filters", {}) or {}
    
    try:
        with SessionLocal() as session:
            items = trade_service.repo.list_open_for_user(
                session,
                user_telegram_id=update.effective_user.id, 
                **filters_map
            )
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
                reply_markup=keyboard, 
                parse_mode=ParseMode.HTML
            )
    except BadRequest as e:
        if "Message is not modified" not in str(e): log.warning(f"Error in navigate_open_recs_handler: {e}")
    except Exception as e:
        log.error(f"Unexpected error in navigate_open_recs_handler: {e}", exc_info=True)

async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = _parse_cq_parts(query.data)
    if len(parts) < 4: await query.answer("Bad request.", show_alert=True); return
    try: 
        rec_id, exit_price = int(parts[2]), parse_number(parts[3])
    except (ValueError, IndexError) as e: 
        await query.answer(f"Invalid value: {e}", show_alert=True); return
    
    await query.answer("Closing recommendation...")
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        rec = trade_service.close(rec_id, exit_price)
        final_text = "✅ Recommendation closed successfully.\n\n" + build_trade_card_text(rec)
        await query.edit_message_text(text=final_text, parse_mode=ParseMode.HTML, reply_markup=None)
    except Exception as e:
        await query.edit_message_text(text=f"❌ Failed to close recommendation: {e}")
    finally:
        context.user_data.pop(AWAITING_INPUT_KEY, None)

async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    rec_id = _parse_tail_int(query.data)
    if rec_id:
        await _send_or_edit_rec_panel(context, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

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
    try: 
        rec_id = int(parts[2])
    except Exception: 
        await query.answer("Invalid request.", show_alert=True); return
    
    if len(parts) > 3 and parts[3] == "remove":
        await query.answer("Removing Profit Stop...")
        trade_service: TradeService = get_service(context, "trade_service")
        trade_service.update_profit_stop(rec_id, None)
        await _send_or_edit_strategy_menu(context, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)
        return
        
    context.user_data[AWAITING_INPUT_KEY] = {"action": "profit_stop", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await query.edit_message_text(f"{query.message.text}\n\n<b>🛡️ Please <u>reply to this message ↩️</u> with the new Profit Stop price.</b>", parse_mode=ParseMode.HTML)

async def set_strategy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Changing strategy...")
    parts = _parse_cq_parts(query.data)
    try: 
        rec_id, strategy_value = int(parts[2]), parts[3]
    except Exception: 
        await query.answer("Invalid request.", show_alert=True); return
    
    trade_service: TradeService = get_service(context, "trade_service")
    trade_service.update_exit_strategy(rec_id, ExitStrategy(strategy_value))
    await _send_or_edit_strategy_menu(context, query.message.chat_id, query.message.message_id, rec_id, query.from_user.id)

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
    
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler), group=1)
# --- END OF FINAL, FULLY RE-ARCHITECTED AND ROBUST FILE ---