# src/capitalguard/interfaces/telegram/conversation_handlers.py
# Version: 31.2 (Stable Publish Token)
# Fix: Prevent regeneration of review_token to eliminate stale publish actions

import asyncio
import uuid
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from src.capitalguard.services.trade_service import TradeService
from src.capitalguard.services.price_service import PriceService
from src.capitalguard.services.market_data_service import MarketDataService
from src.capitalguard.repositories.user_repository import UserRepository
from src.capitalguard.repositories.channel_repository import ChannelRepository
from src.capitalguard.interfaces.telegram import keyboards, ui_texts, helpers, auth
from src.capitalguard.interfaces.telegram.helpers import parse_number, parse_targets_list
from src.capitalguard.utils.decorators import uow_transaction
from src.capitalguard.utils.logs import loge

# --- States ---
SELECT_METHOD, SELECT_ASSET, SELECT_DIRECTION, SELECT_TYPE, ENTER_PRICE, REVIEW, NOTES, I_CHANNEL_PICKER = range(8)
trade_service = TradeService()
price_service = PriceService()
market_service = MarketDataService()
user_repo = UserRepository()
channel_repo = ChannelRepository()

# --- Utility functions ---

async def clean_user_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.chat_data.clear()

async def _disable_previous_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if "last_message_id" in context.user_data:
            msg_id = context.user_data.pop("last_message_id")
            await update.effective_chat.edit_message_reply_markup(msg_id, reply_markup=None)
    except Exception:
        pass

def require_token_match(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            token_in_callback = query.data.split(":")[-1]
            token_in_session = context.user_data.get("review_token")
            if not token_in_session or token_in_session != token_in_callback:
                await query.answer("❌ Stale action. Please start a new recommendation.", show_alert=True)
                return
            return await func(update, context)
        except Exception as e:
            loge("require_token_match", e)
    return wrapper

# --- Core Flow ---

@auth.require_login
async def newrec_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await clean_user_state(update, context)
    await _disable_previous_keyboard(update, context)
    await asyncio.sleep(0.2)
    await update.message.reply_text(
        ui_texts.CHOOSE_METHOD,
        reply_markup=keyboards.method_keyboard(),
    )
    return SELECT_METHOD

async def select_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["method"] = query.data
    await query.edit_message_text(
        text=ui_texts.CHOOSE_ASSET,
        reply_markup=keyboards.asset_keyboard(),
    )
    return SELECT_ASSET

async def select_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["asset"] = query.data
    await query.edit_message_text(
        text=ui_texts.CHOOSE_DIRECTION,
        reply_markup=keyboards.direction_keyboard(),
    )
    return SELECT_DIRECTION

async def select_direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["direction"] = query.data
    await query.edit_message_text(
        text=ui_texts.CHOOSE_TYPE,
        reply_markup=keyboards.type_keyboard(),
    )
    return SELECT_TYPE

async def select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["type"] = query.data
    await query.edit_message_text(ui_texts.ENTER_PRICE)
    return ENTER_PRICE

async def enter_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        price = parse_number(text)
        context.user_data["price"] = price
        await show_review_card(update, context)
        return REVIEW
    except Exception:
        await update.message.reply_text("⚠️ Invalid number. Please re-enter:")
        return ENTER_PRICE

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Generate token once per session only
    if not context.user_data.get("review_token"):
        full_token = str(uuid.uuid4())
        short_token = full_token[:12]
        context.user_data["review_token"] = short_token
    else:
        short_token = context.user_data["review_token"]

    text = helpers.format_review_text(context.user_data)
    keyboard = keyboards.review_final_keyboard(short_token)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        msg = await update.message.reply_text(text, reply_markup=keyboard)
        context.user_data["last_message_id"] = msg.message_id

    return REVIEW

@require_token_match
async def review_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split(":")[0]
    if data == "add_notes":
        await query.edit_message_text("✍️ Send your notes:")
        return NOTES
    elif data == "choose_channels":
        token = context.user_data["review_token"]
        channels = await channel_repo.get_all()
        await query.edit_message_text(
            "📢 Choose channels:",
            reply_markup=keyboards.build_channel_picker_keyboard(channels, token),
        )
        return I_CHANNEL_PICKER
    elif data == "publish":
        return await publish_handler(update, context)
    return REVIEW

async def add_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = update.message.text.strip()
    context.user_data["notes"] = notes
    await show_review_card(update, context)
    return REVIEW

@require_token_match
@uow_transaction
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        user_id = update.effective_user.id
        data = context.user_data
        await trade_service.create_trade(data, user_id)
        await query.edit_message_text("✅ Recommendation published successfully.")
        await clean_user_state(update, context)
        return ConversationHandler.END
    except Exception as e:
        loge("publish_handler", e)
        await query.edit_message_text("⚠️ Error while publishing recommendation.")
        return ConversationHandler.END

# --- Conversation Handler ---

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("newrec", newrec_command_handler)],
    states={
        SELECT_METHOD: [CallbackQueryHandler(select_method)],
        SELECT_ASSET: [CallbackQueryHandler(select_asset)],
        SELECT_DIRECTION: [CallbackQueryHandler(select_direction)],
        SELECT_TYPE: [CallbackQueryHandler(select_type)],
        ENTER_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_price)],
        REVIEW: [CallbackQueryHandler(review_navigation)],
        NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_notes)],
        I_CHANNEL_PICKER: [CallbackQueryHandler(review_navigation)],
    },
    fallbacks=[],
    per_user=True,
)