# src/capitalguard/interfaces/telegram/conversation_handlers.py (v25.6 - FINAL & STATE-SAFE)
"""
Implements all conversational flows for the Telegram bot, primarily for creating recommendations.
This version is hardened against session tampering and state loss by using user-specific,
persistent storage (`context.user_data`) for all conversation state.
"""

import logging
from typing import Dict, Any, Tuple, Optional
from decimal import Decimal, InvalidOperation

from telegram import Update, ReplyKeyboardRemove, User, Message
from telegram.error import BadRequest
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_cq_parts
from .ui_texts import build_review_text_with_price
from .keyboards import (
    review_final_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, main_creation_keyboard
)
from .parsers import parse_quick_command, parse_text_editor, parse_number, parse_targets_list
from .auth import require_active_user, require_analyst_user

from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

# Conversation states
(SELECT_METHOD, AWAIT_TEXT_INPUT, I_ASSET, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_REVIEW) = range(7)

def get_user_draft(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """Securely gets the recommendation draft from user_data, ensuring it exists."""
    if 'new_rec_draft' not in context.user_data:
        context.user_data['new_rec_draft'] = {}
    return context.user_data['new_rec_draft']

def clean_user_state(context: ContextTypes.DEFAULT_TYPE):
    """Cleans up all conversation-related keys from user_data to prevent state leakage."""
    keys_to_pop = ['new_rec_draft', 'last_conv_message', 'input_mode']
    for key in keys_to_pop:
        context.user_data.pop(key, None)

def _get_user_and_message_from_update(update: Update) -> Tuple[Optional[User], Optional[Message]]:
    """Helper to extract user and message objects from an update."""
    if update.callback_query:
        return update.callback_query.from_user, update.callback_query.message
    elif update.message:
        return update.message.from_user, update.message
    return None, None

@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_menu_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """Starts the recommendation creation flow by showing the method selection menu."""
    clean_user_state(context)
    sent_message = await update.message.reply_text(
        "🚀 Create a new recommendation.\nPlease choose your preferred input method:",
        reply_markup=main_creation_keyboard()
    )
    context.user_data['last_conv_message'] = (sent_message.chat_id, sent_message.message_id)
    return SELECT_METHOD

@uow_transaction
@require_active_user
@require_analyst_user
async def start_interactive_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """Entry point for the step-by-step interactive builder."""
    clean_user_state(context)
    user, message_obj = _get_user_and_message_from_update(update)
    if not user or not message_obj: return ConversationHandler.END
    
    trade_service = get_service(context, "trade_service", TradeService)
    recent_assets = trade_service.get_recent_assets_for_user(db_session, user_telegram_id=str(user.id), limit=5)

    reply_method = message_obj.edit_text if update.callback_query else message_obj.reply_text
    sent_message = await reply_method(
        "🚀 Interactive Builder\n\n1️⃣ Select a recent asset or type a new symbol:",
        reply_markup=asset_choice_keyboard(recent_assets)
    )
    if isinstance(sent_message, Message):
        context.user_data['last_conv_message'] = (sent_message.chat_id, sent_message.message_id)
    elif update.callback_query:
        context.user_data['last_conv_message'] = (update.callback_query.message.chat_id, update.callback_query.message.message_id)
    
    return I_ASSET

@uow_transaction
@require_active_user
@require_analyst_user
async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """Entry point for the text-based creation modes."""
    clean_user_state(context)
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data['input_mode'] = command
    if command == 'rec':
        await update.message.reply_text("⚡️ Quick Command Mode\n\nEnter your full recommendation in a single message.")
    elif command == 'editor':
        await update.message.reply_text("📋 Text Editor Mode\n\nPaste your recommendation using field names.")
    return AWAIT_TEXT_INPUT

async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's choice of creation method."""
    query = update.callback_query
    await query.answer()
    choice = query.data.split('_')[1]
    if choice == "interactive":
        return await start_interactive_entrypoint(update, context)
    
    context.user_data['input_mode'] = 'rec' if choice == "quick" else 'editor'
    prompt = "⚡️ Quick Command Mode\n\nEnter your full recommendation." if choice == "quick" else "📋 Text Editor Mode\n\nPaste your recommendation."
    await query.message.edit_text(prompt)
    context.user_data.pop('last_conv_message', None)
    return AWAIT_TEXT_INPUT

async def received_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the full text input from quick command or editor mode."""
    mode = context.user_data.get('input_mode')
    text = update.message.text
    data = None
    if mode == 'rec': data = parse_quick_command(text)
    elif mode == 'editor': data = parse_text_editor(text)
    
    if data:
        draft = get_user_draft(context)
        draft.update(data)
        return await show_review_card(update, context)
        
    await update.message.reply_text("❌ Invalid format. Please check your input and try again.")
    return AWAIT_TEXT_INPUT

async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the asset selection step of the interactive builder."""
    draft = get_user_draft(context)
    asset = ""
    user, message_obj = _get_user_and_message_from_update(update)
    if not user or not message_obj: return ConversationHandler.END

    if update.callback_query:
        await update.callback_query.answer()
        asset = update.callback_query.data.split('_', 1)[1]
        if asset.lower() == "new":
            await message_obj.edit_text("✍️ Please type the new asset symbol (e.g., BTCUSDT).")
            return I_ASSET
    else:
        asset = (update.message.text or "").strip().upper()
        try:
            await update.message.delete()
        except BadRequest:
            pass

    market_data_service = get_service(context, "market_data_service", MarketDataService)
    if not market_data_service.is_valid_symbol(asset, "Futures"):
        if 'last_conv_message' in context.user_data:
            chat_id, message_id = context.user_data['last_conv_message']
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=f"❌ Symbol '{asset}' is not valid. Please select a valid one or type a new one."
            )
        return I_ASSET

    draft['asset'] = asset
    draft['market'] = draft.get('market', 'Futures')

    if 'last_conv_message' in context.user_data:
        chat_id, message_id = context.user_data['last_conv_message']
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"✅ Asset: {asset}\n\n2️⃣ Choose the trade side:",
            reply_markup=side_market_keyboard(draft['market'])
        )
    
    return I_SIDE_MARKET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the side selection step."""
    query = update.callback_query
    await query.answer()
    side = query.data.split('_')[1]
    draft = get_user_draft(context)
    draft['side'] = side
    await query.message.edit_text(f"✅ Asset: {draft.get('asset','N/A')} ({side})\n\n3️⃣ Choose the entry order type:", reply_markup=order_type_keyboard())
    return I_ORDER_TYPE

async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the order type selection step."""
    query = update.callback_query
    await query.answer()
    order_type = query.data.split('_')[1]
    draft = get_user_draft(context)
    draft['order_type'] = order_type
    prompt = ( "4️⃣ Enter prices in a single line:\n<code>STOP  TARGETS...</code>\nE.g., <code>58000 60k@30 62k@50</code>" if order_type == 'MARKET' else "4️⃣ Enter prices in a single line:\n<code>ENTRY  STOP  TARGETS...</code>\nE.g., <code>59k 58k 60k@30 62k@50</code>" )
    await query.message.edit_text(f"✅ Order Type: {order_type}\n\n{prompt}", parse_mode="HTML")
    return I_PRICES

async def prices_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the price input step, including validation and live price fetching."""
    draft = get_user_draft(context)
    order_type = draft.get('order_type', 'LIMIT').upper()
    tokens = (update.message.text or "").strip().replace(',', ' ').split()
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        
        if order_type == 'MARKET':
            if len(tokens) < 2: raise ValueError("MARKET format requires: STOP then TARGETS...")
            stop_loss = Decimal(str(parse_number(tokens[0])))
            targets = parse_targets_list(tokens[1:])
            if not targets: raise ValueError("No valid targets were parsed.")

            price_service = get_service(context, "price_service", PriceService)
            live_price_float = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"), force_refresh=True)
            if live_price_float is None:
                raise ValueError(f"Could not fetch live price for {draft['asset']}. Please try again.")
            live_price = Decimal(str(live_price_float))
            
            trade_service._validate_recommendation_data(draft["side"], live_price, stop_loss, targets)
            
            draft["entry"] = live_price
            draft["stop_loss"] = stop_loss
            draft["targets"] = targets
        else:
            if len(tokens) < 3: raise ValueError("LIMIT/STOP_MARKET format requires: ENTRY STOP then TARGETS...")
            entry = Decimal(str(parse_number(tokens[0])))
            stop_loss = Decimal(str(parse_number(tokens[1])))
            targets = parse_targets_list(tokens[2:])
            if not targets: raise ValueError("No valid targets were parsed.")

            trade_service._validate_recommendation_data(draft["side"], entry, stop_loss, targets)
            
            draft["entry"] = entry
            draft["stop_loss"] = stop_loss
            draft["targets"] = targets

    except (ValueError, InvalidOperation) as e:
        await update.message.reply_text(f"❌ Invalid format or logic: {e}\nPlease try again.")
        return I_PRICES
        
    return await show_review_card(update, context)

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Renders the final review card before publication."""
    user, message = _get_user_and_message_from_update(update)
    if not message: return ConversationHandler.END
    
    draft = get_user_draft(context)
    if not draft or not draft.get("asset"):
        await message.reply_text("Error, please start over with /newrec.")
        clean_user_state(context)
        return ConversationHandler.END
        
    price_service = get_service(context, "price_service", PriceService)
    preview_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
    review_text = build_review_text_with_price(draft, preview_price)
    keyboard = review_final_keyboard()

    target_chat_id, target_message_id = context.user_data.get('last_conv_message', (None, None))
    if not target_chat_id:
        target_chat_id, target_message_id = (message.chat_id, message.message_id)

    try:
        sent_message = await context.bot.edit_message_text(
            chat_id=target_chat_id, message_id=target_message_id,
            text=review_text, reply_markup=keyboard,
            parse_mode='HTML', disable_web_page_preview=True
        )
        if update.message: await update.message.delete()
        if isinstance(sent_message, Message):
            context.user_data['last_conv_message'] = (sent_message.chat_id, sent_message.message_id)
    except BadRequest:
        sent_message = await context.bot.send_message(chat_id=target_chat_id, text=review_text, reply_markup=keyboard, parse_mode='HTML')
        context.user_data['last_conv_message'] = (sent_message.chat_id, sent_message.message_id)
            
    return I_REVIEW

@uow_transaction
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """Final handler that calls the TradeService to publish the recommendation."""
    query = update.callback_query
    await query.answer("Processing...")

    draft = get_user_draft(context)
    if not draft:
        await query.message.edit_text("❌ Your session has expired. Please start over with /newrec.")
        clean_user_state(context)
        return ConversationHandler.END
        
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        saved_rec, report = await trade_service.create_and_publish_recommendation_async(
            user_id=str(query.from_user.id),
            db_session=db_session,
            **draft
        )
        if report.get("success"):
            await query.message.edit_text(f"✅ Recommendation #{saved_rec.id} was created and published successfully.")
        else:
            fail_reason = report.get("failed", [{}])[0].get("reason", "Unknown error.")
            await query.message.edit_text(
                f"⚠️ Recommendation #{saved_rec.id} was saved, but publishing failed.\n<b>Reason:</b> {fail_reason}",
                parse_mode='HTML'
            )
    except Exception as e:
        log.exception("Handler failed to save/publish recommendation.")
        await query.message.edit_text(f"❌ A critical error occurred: {e}.")
    finally:
        clean_user_state(context)
    return ConversationHandler.END

async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cleans up state and exits the conversation."""
    user, message = _get_user_and_message_from_update(update)
    if message:
        if update.callback_query:
            await update.callback_query.answer()
            await message.edit_text("Operation cancelled.")
        else:
            await message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    clean_user_state(context)
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    """Builds and registers the main ConversationHandler for creating recommendations."""
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_menu_entrypoint),
            CommandHandler("new", start_interactive_entrypoint),
            CommandHandler("rec", start_text_input_entrypoint),
            CommandHandler("editor", start_text_input_entrypoint),
        ],
        states={
            SELECT_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            AWAIT_TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_input)],
            I_ASSET: [
                CallbackQueryHandler(asset_chosen, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen)
            ],
            I_SIDE_MARKET: [CallbackQueryHandler(side_chosen, pattern="^side_")],
            I_ORDER_TYPE: [CallbackQueryHandler(order_type_chosen, pattern="^type_")],
            I_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received)],
            I_REVIEW: [
                CallbackQueryHandler(publish_handler, pattern=r"^rec:publish"),
                CallbackQueryHandler(cancel_conv_handler, pattern=r"^rec:cancel")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv_handler)],
        name="recommendation_creation",
        persistent=True,
        per_user=True,
        per_chat=True,
    )
    app.add_handler(conv_handler)

#END