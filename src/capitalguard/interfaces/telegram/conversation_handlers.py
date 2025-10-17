# src/capitalguard/interfaces/telegram/conversation_handlers.py (v35.1 - Fixed Version)
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Set

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from telegram.error import BadRequest
from telegram.constants import ParseMode

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_cq_parts
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, review_final_keyboard,
    build_channel_picker_keyboard
)
from .auth import require_active_user, require_analyst_user
from .parsers import parse_quick_command, parse_text_editor, parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository

log = logging.getLogger(__name__)

# --- Conversation States ---
(
    SELECT_METHOD, AWAIT_TEXT_INPUT, AWAITING_ASSET, AWAITING_SIDE, AWAITING_TYPE,
    AWAITING_PRICES, AWAITING_REVIEW, AWAITING_NOTES, AWAITING_CHANNELS
) = range(9)

# --- State Management Keys ---
DRAFT_KEY = "rec_creation_draft"
CHANNEL_PICKER_KEY = "channel_picker_selection"  # ADDED MISSING CONSTANT

def clean_creation_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(DRAFT_KEY, None)
    context.user_data.pop(CHANNEL_PICKER_KEY, None)  # Clean channel picker state too

# --- Simple Callback Parser (REPLACED CallbackBuilder) ---
def parse_callback_data(callback_data: str) -> Dict[str, Any]:
    """Parse callback data in format 'prefix:action:param1:param2:...'"""
    if not callback_data or ':' not in callback_data:
        return {"action": callback_data, "params": []}
    
    parts = callback_data.split(':')
    return {
        "action": parts[1] if len(parts) > 1 else "",
        "params": parts[2:] if len(parts) > 2 else []
    }

# --- Entry Points ---
@require_active_user
@require_analyst_user
async def newrec_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clean_creation_state(context)
    await update.message.reply_html(
        "🚀 <b>New Recommendation</b>\nChoose an input method:",
        reply_markup=main_creation_keyboard()
    )
    return SELECT_METHOD

@require_active_user
@require_analyst_user
async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clean_creation_state(context)
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data[DRAFT_KEY] = {'input_mode': command}
    prompt = "⚡️ Quick Command Mode\n\nEnter your full recommendation." if command == 'rec' else "📋 Text Editor Mode\n\nPaste your recommendation."
    await update.message.reply_text(prompt)
    return AWAIT_TEXT_INPUT

# --- State Handlers ---

@uow_transaction
@require_active_user
@require_analyst_user
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split('_')[1]
    
    if choice == "interactive":
        trade_service = get_service(context, "trade_service", TradeService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(query.from_user.id))
        await query.edit_message_text(
            "<b>Step 1/4: Asset</b>\nSelect or type the asset symbol (e.g., BTCUSDT).",
            reply_markup=asset_choice_keyboard(recent_assets),
            parse_mode=ParseMode.HTML,
        )
        context.user_data[DRAFT_KEY] = {}
        return AWAITING_ASSET
        
    context.user_data[DRAFT_KEY] = {'input_mode': 'rec' if choice == 'quick' else 'editor'}
    prompt = "⚡️ Quick Command Mode\n\nEnter your full recommendation." if choice == "quick" else "📋 Text Editor Mode\n\nPaste your recommendation."
    await query.edit_message_text(prompt)
    return AWAIT_TEXT_INPUT

async def received_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get(DRAFT_KEY, {})
    mode = draft.get('input_mode')
    text = update.message.text
    data = parse_quick_command(text) if mode == 'rec' else parse_text_editor(text)
    if data:
        draft.update(data)
        await show_review_card(update, context)
        return AWAITING_REVIEW
    await update.message.reply_text("❌ Invalid format. Please try again or /cancel.")
    return AWAIT_TEXT_INPUT

async def asset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data[DRAFT_KEY]
    query = update.callback_query
    message = update.effective_message
    
    if query:
        await query.answer()
        asset = query.data.split("_", 1)[1]
        if asset.lower() == "new":
            await query.edit_message_text("✍️ Please type the new asset symbol.")
            return AWAITING_ASSET
    else:
        asset = (update.message.text or "").strip().upper()

    market_data_service = get_service(context, "market_data_service", MarketDataService)
    if not market_data_service.is_valid_symbol(asset, draft.get("market", "Futures")):
        await message.reply_text(f"❌ Symbol '<b>{asset}</b>' is not valid. Please try again.", parse_mode=ParseMode.HTML)
        return AWAITING_ASSET

    draft['asset'] = asset
    draft['market'] = draft.get('market', 'Futures')
    
    await message.reply_html(
        f"✅ Asset: <b>{asset}</b>\n\n<b>Step 2/4: Side</b>\nChoose the trade direction.",
        reply_markup=side_market_keyboard(draft['market'])
    )
    return AWAITING_SIDE

async def side_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    draft = context.user_data[DRAFT_KEY]
    
    action = query.data.split("_")[1]
    if action in ("LONG", "SHORT"):
        draft['side'] = action
        await query.edit_message_text(
            f"✅ Side: <b>{action}</b>\n\n<b>Step 3/4: Order Type</b>\nChoose the entry order type.",
            reply_markup=order_type_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return AWAITING_TYPE
    elif action == "menu": # change_market_menu
        await query.edit_message_reply_markup(reply_markup=market_choice_keyboard())
        return AWAITING_SIDE

async def market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    draft = context.user_data[DRAFT_KEY]
    
    if "back" in query.data:
        await query.edit_message_reply_markup(reply_markup=side_market_keyboard(draft.get('market', 'Futures')))
        return AWAITING_SIDE
        
    market = query.data.split("_")[1]
    draft['market'] = market
    await query.edit_message_reply_markup(reply_markup=side_market_keyboard(market))
    return AWAITING_SIDE

async def type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    draft = context.user_data[DRAFT_KEY]
    order_type = query.data.split("_")[1]
    draft['order_type'] = order_type
    
    prompt = (
        "<b>Step 4/4: Prices</b>\nEnter: <code>STOP TARGETS...</code>\nEx: <code>58k 60k@50 62k@50</code>"
        if order_type == 'MARKET' else
        "<b>Step 4/4: Prices</b>\nEnter: <code>ENTRY STOP TARGETS...</code>\nEx: <code>59k 58k 60k@50 62k@50</code>"
    )
    await query.edit_message_text(f"✅ Order Type: <b>{order_type}</b>\n\n{prompt}", parse_mode=ParseMode.HTML)
    return AWAITING_PRICES

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data[DRAFT_KEY]
    tokens = (update.message.text or "").strip().split()
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        
        if draft["order_type"] == 'MARKET':
            if len(tokens) < 2: raise ValueError("MARKET format: STOP then TARGETS...")
            stop_loss, targets = parse_number(tokens[0]), parse_targets_list(tokens[1:])
            price_service = get_service(context, "price_service", PriceService)
            live_price_float = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"), True)
            if not live_price_float: raise ValueError("Could not fetch live market price.")
            live_price = Decimal(str(live_price_float))
            trade_service._validate_recommendation_data(draft["side"], live_price, stop_loss, targets)
            draft.update({"entry": live_price, "stop_loss": stop_loss, "targets": targets})
        else:
            if len(tokens) < 3: raise ValueError("LIMIT/STOP format: ENTRY, STOP, then TARGETS...")
            entry, stop_loss = parse_number(tokens[0]), parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            trade_service._validate_recommendation_data(draft["side"], entry, stop_loss, targets)
            draft.update({"entry": entry, "stop_loss": stop_loss, "targets": targets})
            
        if not draft.get("targets"): raise ValueError("No valid targets parsed.")
        
        await show_review_card(update, context)
        return AWAITING_REVIEW
        
    except (ValueError, InvalidOperation, TypeError) as e:
        await update.message.reply_text(f"⚠️ {str(e)}\nPlease try again.")
        return AWAITING_PRICES

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data[DRAFT_KEY]
    if not draft.get("token"):
        draft["token"] = str(uuid.uuid4())[:8]  # FIXED: uuid now imported
    
    price_service = get_service(context, "price_service", PriceService)
    preview_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
    
    review_text = build_review_text_with_price(draft, preview_price)
    
    await update.effective_message.reply_html(review_text, reply_markup=review_final_keyboard(draft["token"]))

@uow_transaction
@require_active_user
@require_analyst_user
async def review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get(DRAFT_KEY)
    
    # FIXED: Replaced CallbackBuilder with parse_callback_data
    callback_data = parse_callback_data(query.data)
    action = callback_data.get('action')
    token_in_callback = callback_data.get('params', [None])[0]

    if not draft or draft.get('token') != token_in_callback:
        await query.edit_message_text("❌ Stale action. Please start a new recommendation with /newrec.")
        clean_creation_state(context)
        return ConversationHandler.END

    if action == "publish":
        trade_service = get_service(context, "trade_service", TradeService)
        rec = None
        try:
            # FIXED: Use the defined CHANNEL_PICKER_KEY constant
            draft['target_channel_ids'] = context.user_data.get(CHANNEL_PICKER_KEY, set())
            rec, report = await trade_service.create_and_publish_recommendation_async(
                user_id=str(query.from_user.id), 
                db_session=db_session, 
                **draft
            )
            if report.get("success"):
                await query.edit_message_text(
                    f"✅ Recommendation #{rec.id} for <b>{rec.asset.value}</b> published.", 
                    parse_mode=ParseMode.HTML
                )
            else:
                await query.edit_message_text(
                    f"⚠️ Rec #{rec.id} saved, but publishing failed: {report.get('failed', [{}])[0].get('reason', 'Unknown')}"
                )
        except Exception as e:
            log.exception("Publish handler failed")
            error_msg = f"❌ A critical error occurred: {e}"
            if rec:
                error_msg = f"❌ Failed to publish recommendation #{rec.id}: {e}"
            await query.edit_message_text(error_msg)
        finally:
            clean_creation_state(context)
        return ConversationHandler.END
    
    elif action == "add_notes":
        await query.edit_message_text(
            f"{query.message.text}\n\n✍️ Please send your notes for this recommendation.", 
            parse_mode=ParseMode.HTML
        )
        return AWAITING_NOTES

    elif action == "choose_channels":
        user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        selected_ids = context.user_data.setdefault(CHANNEL_PICKER_KEY, {
            ch.telegram_channel_id for ch in all_channels if ch.is_active
        })
        keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids)
        await query.edit_message_text("📢 Select channels for publication:", reply_markup=keyboard)
        return AWAITING_CHANNELS

    elif action == "cancel":
        await query.edit_message_text("Operation cancelled.")
        clean_creation_state(context)
        return ConversationHandler.END

async def notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data[DRAFT_KEY]
    draft['notes'] = (update.message.text or '').strip()
    await show_review_card(update, context)
    return AWAITING_REVIEW

@uow_transaction
@require_active_user
@require_analyst_user
async def channel_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get(DRAFT_KEY)
    
    # FIXED: Replaced CallbackBuilder with parse_callback_data
    callback_data = parse_callback_data(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    token_in_callback = params[0] if params else None

    if not draft or draft.get('token') != token_in_callback:
        await query.edit_message_text("❌ Stale action. Please start a new recommendation with /newrec.")
        clean_creation_state(context)
        return ConversationHandler.END

    if action == "back":
        await show_review_card(update, context)
        return AWAITING_REVIEW

    if action == "confirm":
        await show_review_card(update, context)
        return AWAITING_REVIEW

    if action == "toggle":
        selected_ids = context.user_data.get(CHANNEL_PICKER_KEY, set())
        channel_id, page = int(params[1]), int(params[2])
        if channel_id in selected_ids: 
            selected_ids.remove(channel_id)
        else: 
            selected_ids.add(channel_id)
        context.user_data[CHANNEL_PICKER_KEY] = selected_ids
        
        user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids, page=page)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return AWAITING_CHANNELS

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clean_creation_state(context)
    await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entrypoint),
            CommandHandler("rec", start_text_input_entrypoint),
            CommandHandler("editor", start_text_input_entrypoint),
        ],
        states={
            SELECT_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            AWAIT_TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_input)],
            AWAITING_ASSET: [
                CallbackQueryHandler(asset_handler, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_handler)
            ],
            AWAITING_SIDE: [
                CallbackQueryHandler(side_handler, pattern="^side_"),
                CallbackQueryHandler(market_handler, pattern="^market_")
            ],
            AWAITING_TYPE: [CallbackQueryHandler(type_handler, pattern="^type_")],
            AWAITING_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_handler)],
            AWAITING_REVIEW: [CallbackQueryHandler(review_handler, pattern=r"^rec:")],
            AWAITING_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_handler)],
            AWAITING_CHANNELS: [CallbackQueryHandler(channel_picker_handler, pattern=r"^pub:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        name="recommendation_creation_v3.5",
        per_user=True,
        per_chat=True,
    )
    app.add_handler(conv_handler)