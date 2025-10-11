# src/capitalguard/interfaces/telegram/conversation_handlers.py (v26.7 - COMPLETE, FINAL & UOW-COMPLIANT)
"""
Implements the conversational flow for creating a new recommendation (/newrec).
This version fixes a critical TypeError in the final publish step by correctly
calling the decorated service method without redundant arguments.
"""

import logging
import asyncio
import uuid
from decimal import Decimal, InvalidOperation
from typing import Dict, Any

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (Application, ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters)
from telegram.error import BadRequest

from capitalguard.infrastructure.db.uow import uow_transaction, session_scope
from .helpers import get_service
from .ui_texts import build_review_text_with_price
from .keyboards import (main_creation_keyboard, asset_choice_keyboard, side_market_keyboard, order_type_keyboard, review_final_keyboard)
from .auth import get_db_user
from capitalguard.infrastructure.db.models import UserType
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from .commands import start_cmd, myportfolio_cmd, help_cmd

log = logging.getLogger(__name__)

(SELECT_METHOD, I_ASSET, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_REVIEW) = range(6)

def get_user_draft(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    return context.user_data.setdefault('new_rec_draft', {})

def clean_user_state(context: ContextTypes.DEFAULT_TYPE):
    for key in ['new_rec_draft', 'last_conv_message', 'review_token']:
        context.user_data.pop(key, None)

async def newrec_menu_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    with session_scope() as db_session:
        db_user = get_db_user(update, context, db_session)
        if not db_user or not db_user.is_active or db_user.user_type != UserType.ANALYST:
            await update.message.reply_html("🚫 <b>Permission Denied:</b> This command is for active analysts only.")
            return ConversationHandler.END
    clean_user_state(context)
    sent_message = await update.message.reply_html("🚀 <b>New Recommendation</b>\nChoose an input method:", reply_markup=main_creation_keyboard())
    context.user_data['last_conv_message'] = (sent_message.chat_id, sent_message.message_id)
    return SELECT_METHOD

async def start_interactive_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    with session_scope() as db_session:
        db_user = get_db_user(update, context, db_session)
        if not db_user or not db_user.is_active or db_user.user_type != UserType.ANALYST: return ConversationHandler.END
        trade_service = get_service(context, "trade_service", TradeService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(update.effective_user.id))

    message_obj = update.callback_query.message
    await update.callback_query.answer()
    sent_message = await message_obj.edit_text("<b>Step 1/4: Asset</b>\nSelect or type the asset symbol (e.g., BTCUSDT).", reply_markup=asset_choice_keyboard(recent_assets), parse_mode='HTML')
    context.user_data['last_conv_message'] = (sent_message.chat_id, sent_message.message_id)
    return I_ASSET

async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft, message_obj = get_user_draft(context), update.callback_query.message if update.callback_query else update.message
    asset = ""
    if update.callback_query:
        await update.callback_query.answer()
        asset = update.callback_query.data.split('_', 1)[1]
        if asset.lower() == "new":
            await message_obj.edit_text("✍️ Please type the new asset symbol.")
            return I_ASSET
    else:
        asset = (update.message.text or "").strip().upper()
        try: await update.message.delete()
        except Exception: pass
    market_data_service = get_service(context, "market_data_service", MarketDataService)
    if not market_data_service.is_valid_symbol(asset, draft.get('market', 'Futures')):
        await message_obj.edit_text(f"❌ Symbol '<b>{asset}</b>' is not valid. Please try again.", parse_mode='HTML')
        return I_ASSET
    draft['asset'], draft['market'] = asset, draft.get('market', 'Futures')
    await message_obj.edit_text(f"✅ Asset: <b>{asset}</b>\n\n<b>Step 2/4: Side</b>\nChoose the trade direction.", reply_markup=side_market_keyboard(draft['market']), parse_mode='HTML')
    return I_SIDE_MARKET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, draft = update.callback_query, get_user_draft(context)
    await query.answer()
    draft['side'] = query.data.split('_')[1]
    await query.message.edit_text(f"✅ Asset: <b>{draft['asset']} ({draft['side']})</b>\n\n<b>Step 3/4: Order Type</b>\nChoose the entry order type.", reply_markup=order_type_keyboard(), parse_mode='HTML')
    return I_ORDER_TYPE

async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, draft = update.callback_query, get_user_draft(context)
    await query.answer()
    draft['order_type'] = query.data.split('_')[1]
    prompt = ("<b>Step 4/4: Prices</b>\nEnter in one line: <code>STOP TARGETS...</code>\nE.g., <code>58k 60k@30 62k@50</code>" if draft['order_type'] == 'MARKET' else "<b>Step 4/4: Prices</b>\nEnter in one line: <code>ENTRY STOP TARGETS...</code>\nE.g., <code>59k 58k 60k@30 62k@50</code>")
    await query.message.edit_text(f"✅ Order Type: <b>{draft['order_type']}</b>\n\n{prompt}", parse_mode="HTML")
    return I_PRICES

async def prices_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft, tokens = get_user_draft(context), (update.message.text or "").strip().split()
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        if draft['order_type'] == 'MARKET':
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
        if not draft.get("targets"): raise ValueError("No valid targets were parsed.")
    except (ValueError, InvalidOperation, TypeError) as e:
        await update.message.reply_text(f"❌ **Invalid Input:** {e}\nPlease try again.")
        return I_PRICES
    return await show_review_card(update, context)

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.callback_query.message if update.callback_query else update.message
    draft = get_user_draft(context)
    review_token = str(uuid.uuid4())
    context.user_data['review_token'] = review_token
    price_service = get_service(context, "price_service", PriceService)
    preview_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
    review_text = build_review_text_with_price(draft, preview_price)
    target_chat_id, target_message_id = context.user_data.get('last_conv_message', (message.chat_id, message.message_id))
    try:
        sent_message = await context.bot.edit_message_text(chat_id=target_chat_id, message_id=target_message_id, text=review_text, reply_markup=review_final_keyboard(review_token), parse_mode='HTML')
        if update.message: await update.message.delete()
    except BadRequest:
        sent_message = await context.bot.send_message(chat_id=target_chat_id, text=review_text, reply_markup=review_final_keyboard(review_token), parse_mode='HTML')
    context.user_data['last_conv_message'] = (sent_message.chat_id, sent_message.message_id)
    return I_REVIEW

@uow_transaction
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    query = update.callback_query
    await query.answer("Publishing...")
    
    token_in_callback = query.data.split(':')[-1]
    if context.user_data.get('review_token') != token_in_callback:
        await query.edit_message_text("❌ Stale action. Please start a new recommendation.")
        clean_user_state(context)
        return ConversationHandler.END
        
    draft = get_user_draft(context)
    trade_service = get_service(context, "trade_service", TradeService)
    try:
        # ✅ THE FIX: Call the decorated service method WITHOUT passing 'db_session' explicitly.
        rec, report = await trade_service.create_and_publish_recommendation_async(str(query.from_user.id), **draft)
        if report.get("success"): await query.message.edit_text(f"✅ Recommendation #{rec.id} for <b>{rec.asset.value}</b> published.", parse_mode='HTML')
        else: await query.message.edit_text(f"⚠️ Rec #{rec.id} saved, but publishing failed: {report.get('failed', [{}])[0].get('reason')}", parse_mode='HTML')
    except Exception as e:
        log.exception("Handler failed to publish recommendation.")
        await query.message.edit_text(f"❌ A critical error occurred: {e}.")
    finally:
        clean_user_state(context)
    return ConversationHandler.END

async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.callback_query.message if update.callback_query else update.message
    if update.callback_query: await update.callback_query.answer()
    if last_msg_info := context.user_data.get('last_conv_message'):
        try: await context.bot.edit_message_text("Operation cancelled.", chat_id=last_msg_info[0], message_id=last_msg_info[1])
        except Exception: await message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    else: await message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    clean_user_state(context)
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newrec", newrec_menu_entrypoint)],
        states={
            SELECT_METHOD: [CallbackQueryHandler(start_interactive_entrypoint, pattern="^method_interactive")],
            I_ASSET: [CallbackQueryHandler(asset_chosen, pattern="^asset_"), MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen)],
            I_SIDE_MARKET: [CallbackQueryHandler(side_chosen, pattern="^side_")],
            I_ORDER_TYPE: [CallbackQueryHandler(order_type_chosen, pattern="^type_")],
            I_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received)],
            I_REVIEW: [CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"), CallbackQueryHandler(cancel_conv_handler, pattern=r"^rec:cancel")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv_handler),
            CommandHandler("start", start_cmd),
            CommandHandler(["myportfolio", "open"], myportfolio_cmd),
            CommandHandler("help", help_cmd),
        ],
        name="recommendation_creation",
        persistent=True,
        per_user=True,
        per_chat=True,
    )
    app.add_handler(conv_handler)