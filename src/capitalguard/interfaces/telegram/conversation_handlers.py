# --- START OF FINAL, ROBUST FILE USING SERVICE REGISTRY (Version 9.3.0) ---
# src/capitalguard/interfaces/telegram/conversation_handlers.py

import logging
import uuid
import types
from typing import List, Dict, Any, Set

from telegram import Update, ReplyKeyboardRemove
from telegram.error import BadRequest
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)

from .helpers import get_service
from .ui_texts import build_review_text_with_price
from .keyboards import (
    review_final_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, build_channel_picker_keyboard,
    main_creation_keyboard
)
from .parsers import parse_quick_command, parse_text_editor, parse_number, parse_targets_list
from .auth import ALLOWED_USER_FILTER

# ✅ Import service types for type-safe access
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

# --- State Definitions for the Conversation ---
(SELECT_METHOD, AWAIT_TEXT_INPUT, I_ASSET, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW, I_CHANNEL_PICKER) = range(9)
CONVERSATION_DATA_KEY = "new_rec_draft"
REV_TOKENS_MAP = "review_tokens_map"
REV_TOKENS_REVERSE = "review_tokens_rev"

# --- Helper Functions for State Management ---

def _clean_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    """A centralized function to clean up all conversation-related data from user_data and bot_data."""
    context.user_data.pop(CONVERSATION_DATA_KEY, None)
    review_key = context.user_data.pop('current_review_key', None)
    if review_key: context.bot_data.pop(review_key, None)
    review_token = context.user_data.pop('current_review_token', None)
    if review_token: context.user_data.pop(f"pubsel:{review_token}", None)
    context.user_data.pop('original_query_message', None)
    context.user_data.pop('input_mode', None)

def _ensure_token_maps(context: ContextTypes.DEFAULT_TYPE) -> None:
    if REV_TOKENS_MAP not in context.bot_data: context.bot_data[REV_TOKENS_MAP] = {}
    if REV_TOKENS_REVERSE not in context.bot_data: context.bot_data[REV_TOKENS_REVERSE] = {}

def _get_or_make_token_for_review(context: ContextTypes.DEFAULT_TYPE, review_key: str) -> str:
    _ensure_token_maps(context)
    rev_map: Dict[str, str] = context.bot_data[REV_TOKENS_REVERSE]
    tok_map: Dict[str, str] = context.bot_data[REV_TOKENS_MAP]
    if review_key in rev_map: return rev_map[review_key]
    candidate = uuid.uuid4().hex[:8]
    while candidate in tok_map: candidate = uuid.uuid4().hex[:8]
    tok_map[candidate] = review_key
    rev_map[review_key] = candidate
    return candidate

def _resolve_review_key_from_token(context: ContextTypes.DEFAULT_TYPE, token: str) -> str | None:
    _ensure_token_maps(context)
    return context.bot_data[REV_TOKENS_MAP].get(token)

# --- Entry Point and State Handlers ---

async def newrec_menu_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    await update.message.reply_text(
        "🚀 Create a new recommendation.\n\nPlease choose your preferred input method:",
        reply_markup=main_creation_keyboard()
    )
    return SELECT_METHOD

async def start_interactive_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    # ✅ UPDATED: Use the new type-safe service getter
    trade_service = get_service(context, "trade_service", TradeService)
    user_id = str(update.effective_user.id)
    recent_assets = trade_service.get_recent_assets_for_user(user_id, limit=5)
    
    message_obj = update.message or update.callback_query.message
    await message_obj.reply_text(
        "🚀 Interactive Builder\n\n1️⃣ Select a recent asset or type a new symbol:",
        reply_markup=asset_choice_keyboard(recent_assets)
    )
    return I_ASSET

async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data['input_mode'] = command
    
    if command == 'rec':
        await update.message.reply_text("⚡️ Quick Command Mode\n\nEnter your full recommendation in a single message starting with /rec")
    elif command == 'editor':
        await update.message.reply_text("📋 Text Editor Mode\n\nPaste your recommendation using field names.")
        
    return AWAIT_TEXT_INPUT

async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split('_')[1]

    if choice == "interactive":
        return await start_interactive_entrypoint(query, context)
    
    context.user_data['input_mode'] = 'rec' if choice == "quick" else 'editor'
    prompt = "⚡️ Quick Command Mode\n\nEnter your full recommendation in a single message starting with /rec" if choice == "quick" else "📋 Text Editor Mode\n\nPaste your recommendation using field names."
    await query.message.edit_text(prompt)
    return AWAIT_TEXT_INPUT

async def received_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    mode = context.user_data.get('input_mode')
    text = update.message.text
    data = None
    if mode == 'rec': data = parse_quick_command(text)
    elif mode == 'editor': data = parse_text_editor(text)
    
    if data:
        context.user_data[CONVERSATION_DATA_KEY] = data
        return await show_review_card(update, context)

    await update.message.reply_text("❌ Invalid format. Please try again.")
    return AWAIT_TEXT_INPUT

async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    asset = ""
    message_obj = update.message or update.callback_query.message
    
    if update.callback_query:
        await update.callback_query.answer()
        asset = update.callback_query.data.split('_', 1)[1]
        if asset.lower() == "new":
            await message_obj.edit_text("✍️ Please type the new asset symbol (e.g., BTCUSDT).")
            return I_ASSET
    else:
        asset = (update.message.text or "").strip().upper()

    market_data_service = get_service(context, "market_data_service", MarketDataService)
    if not market_data_service.is_valid_symbol(asset, "Futures"):
        await message_obj.reply_text(f"❌ Symbol '{asset}' is not valid. Please try again.")
        return I_ASSET

    draft['asset'] = asset
    draft['market'] = draft.get('market', 'Futures')
    context.user_data[CONVERSATION_DATA_KEY] = draft
    await message_obj.reply_text(f"✅ Asset: {asset}\n\n2️⃣ Choose the trade side:", reply_markup=side_market_keyboard(draft['market']))
    return I_SIDE_MARKET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    side = query.data.split('_')[1]
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    draft['side'] = side
    context.user_data[CONVERSATION_DATA_KEY] = draft
    await query.message.edit_text(f"✅ Asset: {draft.get('asset','N/A')} ({side})\n\n3️⃣ Choose the entry order type:", reply_markup=order_type_keyboard())
    return I_ORDER_TYPE
    
async def change_market_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_reply_markup(reply_markup=market_choice_keyboard())
    return I_SIDE_MARKET

async def market_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    market = draft.get('market', 'Futures')
    if choice != "market_back":
        market = choice.split('_')[1]
    draft['market'] = market
    context.user_data[CONVERSATION_DATA_KEY] = draft
    await query.message.edit_reply_markup(reply_markup=side_market_keyboard(market))
    return I_SIDE_MARKET

async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    order_type = query.data.split('_')[1]
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    draft['order_type'] = order_type
    context.user_data[CONVERSATION_DATA_KEY] = draft

    prompt = ( "4️⃣ Enter prices in a single line:\n<code>STOP  TARGETS...</code>\nE.g., <code>58000 60k@30 62k@50</code>" if order_type == 'MARKET' else "4️⃣ Enter prices in a single line:\n<code>ENTRY  STOP  TARGETS...</code>\nE.g., <code>59k 58k 60k@30 62k@50</code>" )
    await query.message.edit_text(f"✅ Order Type: {order_type}\n\n{prompt}", parse_mode="HTML")
    return I_PRICES

async def prices_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    order_type = draft.get('order_type', 'LIMIT').upper()
    tokens = (update.message.text or "").strip().replace(',', ' ').split()
    try:
        if order_type == 'MARKET':
            if len(tokens) < 2: raise ValueError("MARKET format requires: STOP then TARGETS...")
            draft["entry"] = 0
            draft["stop_loss"] = parse_number(tokens[0])
            draft["targets"] = parse_targets_list(tokens[1:])
        else:
            if len(tokens) < 3: raise ValueError("LIMIT/STOP_MARKET format requires: ENTRY STOP then TARGETS...")
            draft["entry"] = parse_number(tokens[0])
            draft["stop_loss"] = parse_number(tokens[1])
            draft["targets"] = parse_targets_list(tokens[2:])
        if not draft["targets"]: raise ValueError("No valid targets were parsed.")
        
        trade_service = get_service(context, "trade_service", TradeService)
        try:
            trade_service._validate_recommendation_data(
                draft["side"], draft["entry"], draft["stop_loss"], draft["targets"]
            )
        except ValueError as e:
            await update.message.reply_text(f"❌ Invalid Logic: {e}\nPlease check your prices and try again.")
            return I_PRICES

    except ValueError as e:
        await update.message.reply_text(f"❌ Invalid format: {e}\nPlease try again.")
        return I_PRICES
        
    context.user_data[CONVERSATION_DATA_KEY] = draft
    return await show_review_card(update, context)

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False) -> int:
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if not message: return ConversationHandler.END
    
    review_key = context.user_data.get('current_review_key')
    data = context.bot_data.get(review_key) if review_key else context.user_data.get(CONVERSATION_DATA_KEY, {})
    
    if not data or not data.get("asset"):
        await message.reply_text("Error, please start over with /newrec.")
        _clean_conversation_state(context)
        return ConversationHandler.END
        
    price_service = get_service(context, "price_service", PriceService)
    preview_price = await price_service.get_cached_price(data["asset"], data.get("market", "Futures"))
    review_text = build_review_text_with_price(data, preview_price)
    
    if not review_key:
        review_key = str(uuid.uuid4())
        context.user_data['current_review_key'] = review_key
        context.bot_data[review_key] = data.copy()
        
    review_token = _get_or_make_token_for_review(context, review_key)
    keyboard = review_final_keyboard(review_token)
    
    try:
        if is_edit: await message.edit_text(text=review_text, reply_markup=keyboard, parse_mode='HTML', disable_web_page_preview=True)
        else: await message.reply_html(text=review_text, reply_markup=keyboard, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"Edit failed, sending new message. Error: {e}")
            await message.reply_html(text=review_text, reply_markup=keyboard, disable_web_page_preview=True)
            
    return I_REVIEW

async def add_notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    token = query.data.split(':')[2]
    review_key = _resolve_review_key_from_token(context, token)
    if not review_key or review_key not in context.bot_data:
        await query.message.edit_text("❌ This card has expired."); return ConversationHandler.END
    context.user_data['current_review_key'] = review_key
    context.user_data['current_review_token'] = token
    context.user_data['original_query_message'] = query.message
    await query.message.edit_text(f"{query.message.text}\n\n✍️ Please send your notes now.", parse_mode='HTML', disable_web_page_preview=True)
    return I_NOTES

async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    notes = update.message.text.strip()
    review_key = context.user_data.get('current_review_key')
    original_message = context.user_data.pop('original_query_message', None)
    if review_key and review_key in context.bot_data and original_message:
        draft = context.bot_data[review_key]
        draft['notes'] = notes if notes.lower() not in ['skip', 'none'] else None
        try: await update.message.delete()
        except Exception: pass
        dummy_update = Update(update.update_id, callback_query=types.SimpleNamespace(message=original_message, data=''))
        return await show_review_card(dummy_update, context, is_edit=True)
    await update.message.reply_text("An error occurred. Please start over with /newrec.")
    _clean_conversation_state(context)
    return ConversationHandler.END

async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Processing...")
    token = query.data.split(":")[2]
    review_key = _resolve_review_key_from_token(context, token)
    draft = context.bot_data.get(review_key) if review_key else None
    if not draft:
        await query.edit_message_text("❌ This card has expired. Please start over with /newrec.")
        _clean_conversation_state(context)
        return ConversationHandler.END
        
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        saved_rec, report = await trade_service.create_and_publish_recommendation_async(
            user_id=str(update.effective_user.id), **draft
        )
        if report.get("success"):
            await query.edit_message_text(f"✅ Recommendation #{saved_rec.id} was created and published successfully to {len(report['success'])} channel(s).")
        else:
            fail_reason = "No active channels found or failed to post."
            if report.get("failed"):
                fail_reason = report["failed"][0].get("reason", fail_reason)
            await query.edit_message_text(
                f"⚠️ Recommendation #{saved_rec.id} was saved, but publishing failed.\n"
                f"<b>Reason:</b> {fail_reason}\n\n"
                "<i>Please check that the bot is an admin in your channel with posting rights.</i>",
                parse_mode='HTML'
            )
    except Exception as e:
        log.exception("Handler failed to save/publish recommendation.")
        await query.edit_message_text(f"❌ A critical error occurred: {e}")
    finally:
        _clean_conversation_state(context)
    return ConversationHandler.END

async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    message = update.message or (update.callback_query and update.callback_query.message)
    if not message: return ConversationHandler.END
    
    if update.callback_query:
        await update.callback_query.answer()
        try: await message.edit_text("Operation cancelled.")
        except BadRequest: pass
    else:
        await message.reply_text("Current operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def unexpected_input_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get(CONVERSATION_DATA_KEY) or context.user_data.get('current_review_key'):
        await update.message.reply_text("⚠️ Unexpected input. The current creation process has been cancelled.")
    _clean_conversation_state(context)
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_menu_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("new", start_interactive_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("rec", start_text_input_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("editor", start_text_input_entrypoint, filters=ALLOWED_USER_FILTER),
        ],
        states={
            SELECT_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            AWAIT_TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_input)],
            I_ASSET: [
                CallbackQueryHandler(asset_chosen, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen)
            ],
            I_SIDE_MARKET: [
                CallbackQueryHandler(side_chosen, pattern="^side_"),
                CallbackQueryHandler(change_market_menu, pattern="^change_market_menu$"),
                CallbackQueryHandler(market_chosen, pattern="^market_")
            ],
            I_ORDER_TYPE: [CallbackQueryHandler(order_type_chosen, pattern="^type_")],
            I_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received)],
            I_REVIEW: [
                CallbackQueryHandler(add_notes_handler, pattern=r"^rec:add_notes:"),
                CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"),
                CallbackQueryHandler(cancel_conv_handler, pattern=r"^rec:cancel:")
            ],
            I_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv_handler),
            MessageHandler(filters.COMMAND, unexpected_input_fallback)
        ],
        name="recommendation_creation",
        persistent=False,
    )
    app.add_handler(conv_handler)

# --- END OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.2) ---