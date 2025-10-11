# src/capitalguard/interfaces/telegram/forward_parsing_handler.py (v1.2 - COMPLETE, FINAL & UOW-COMPLIANT)
"""
Handles the user flow for parsing a trade signal from a forwarded message.
This version fixes a critical TypeError by correctly calling the decorated
service method without redundant arguments.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from .auth import require_active_user

from capitalguard.application.services.image_parsing_service import ImageParsingService
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

async def forwarded_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message.text or len(message.text) < 20: return
    if context.user_data and any(isinstance(k, tuple) and k[-1] == 'state' for k in context.user_data):
         log.debug("Forwarded message ignored because a conversation is active.")
         return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔬 Analyze as Trade Signal", callback_data="fwd_parse:analyze"),
        InlineKeyboardButton("❌ Ignore", callback_data="fwd_parse:ignore"),
    ]])
    context.user_data['fwd_msg_text'] = message.text
    await message.reply_text("Forwarded message detected. What would you like to do?", reply_markup=keyboard, reply_to_message_id=message.message_id)

@uow_transaction
@require_active_user
async def analyze_forward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    query = update.callback_query
    await query.answer("Analyzing...")
    original_text = context.user_data.get('fwd_msg_text')
    if not original_text:
        await query.edit_message_text("❌ Error: Original message could not be found in context.")
        return
    parsing_service = get_service(context, "image_parsing_service", ImageParsingService)
    trade_data = await parsing_service.extract_trade_data(original_text)
    if not trade_data:
        await query.edit_message_text("❌ Analysis failed: Could not recognize a valid trade signal in the message.")
        context.user_data.pop('fwd_msg_text', None)
        return
    context.user_data['pending_trade'] = trade_data
    asset, side = trade_data['asset'], trade_data['side']
    side_emoji = "📈" if side == "LONG" else "📉"
    confirmation_text = (
        f"{side_emoji} <b>Signal Parsed</b>\n\n"
        f"<b>Asset:</b> {asset}\n<b>Direction:</b> {side}\n"
        f"<b>Entry:</b> {trade_data['entry']:g}\n<b>Stop Loss:</b> {trade_data['stop_loss']:g}\n\n"
        f"Add to your personal portfolio?"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, Confirm & Track", callback_data="fwd_parse:confirm"),
        InlineKeyboardButton("❌ No, Cancel", callback_data="fwd_parse:cancel")
    ]])
    await query.edit_message_text(confirmation_text, reply_markup=keyboard, parse_mode='HTML')

@uow_transaction
@require_active_user
async def confirm_parsed_trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer("Saving...")
    
    trade_data = context.user_data.pop('pending_trade', None)
    context.user_data.pop('fwd_msg_text', None)

    if not trade_data:
        await query.edit_message_text("❌ Session expired. Please forward the message again.")
        return

    trade_service = get_service(context, "trade_service", TradeService)
    # ✅ THE FIX: Call the decorated service method WITHOUT passing 'db_session' explicitly.
    result = await trade_service.create_trade_from_forwarding(user_id=str(db_user.telegram_user_id), trade_data=trade_data, db_session=db_session)

    if result.get('success'):
        await query.edit_message_text(f"✅ <b>Trade #{result['trade_id']} for {result['asset']}</b> has been added to your portfolio!", parse_mode='HTML')
    else:
        await query.edit_message_text(f"❌ <b>Error:</b> {result.get('error', 'Unknown')}", parse_mode='HTML')

async def ignore_or_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop('pending_trade', None)
    context.user_data.pop('fwd_msg_text', None)
    await query.edit_message_text("Operation cancelled.")

def register_forward_parsing_handlers(app: Application):
    app.add_handler(MessageHandler(filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, forwarded_message_handler), group=1)
    app.add_handler(CallbackQueryHandler(analyze_forward_callback, pattern="^fwd_parse:analyze$"))
    app.add_handler(CallbackQueryHandler(confirm_parsed_trade_callback, pattern="^fwd_parse:confirm$"))
    app.add_handler(CallbackQueryHandler(ignore_or_cancel_callback, pattern=r"^fwd_parse:(ignore|cancel)$"))