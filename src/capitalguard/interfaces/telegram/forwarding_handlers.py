# src/capitalguard/interfaces/telegram/forwarding_handlers.py (v25.5 - FINAL & CORRECTED)
"""
Handles the conversation flow for parsing a forwarded message and creating a
personal user trade for tracking.
"""

import logging
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ConversationHandler
)

# ✅ **THE FIX:** Import the decorator from its definitive source.
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from .auth import require_active_user
from capitalguard.application.services.image_parsing_service import ImageParsingService
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

# Conversation states
AWAITING_CONFIRMATION = 1

@require_active_user
async def forwarding_entry_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the forwarding conversation. Parses the message."""
    message = update.message
    log.info(f"🔄 Processing forwarded message from user {update.effective_user.id}")
    
    parsing_service = get_service(context, "image_parsing_service", ImageParsingService)
    
    content = message.text or ""
    if not content:
        await update.message.reply_text("❌ Forwarded message contains no text to parse.")
        return ConversationHandler.END
        
    processing_msg = await update.message.reply_text("🔄 Analyzing signal...")
        
    trade_data = await parsing_service.extract_trade_data(content, is_image=False)
    
    if not trade_data:
        await processing_msg.edit_text("❌ Could not recognize trade data in this message.")
        return ConversationHandler.END
        
    context.user_data['pending_trade'] = trade_data
    
    asset = trade_data['asset']
    side = trade_data['side']
    side_emoji = "📈" if side == "LONG" else "📉"
    
    confirmation_text = (
        f"{side_emoji} <b>Signal Parsed Successfully</b>\n\n"
        f"<b>Asset:</b> {asset}\n"
        f"<b>Direction:</b> {side}\n"
        f"<b>Entry:</b> {trade_data['entry']:g}\n"
        f"<b>Stop Loss:</b> {trade_data['stop_loss']:g}\n"
    )
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm & Track", callback_data="confirm_forwarded_trade"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_forwarded_trade")
    ]])
    
    await processing_msg.edit_text(
        confirmation_text,
        reply_markup=keyboard,
        parse_mode='HTML'
    )
    
    return AWAITING_CONFIRMATION

@uow_transaction
async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session) -> int:
    """Handles the confirmation to add the parsed trade to the user's portfolio."""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    trade_data = context.user_data.get('pending_trade')
    
    if not trade_data:
        await query.edit_message_text("❌ Data expired. Please forward the message again.")
        return ConversationHandler.END
        
    trade_service = get_service(context, "trade_service", TradeService)
    result = await trade_service.create_trade_from_forwarding(user_id, trade_data, db_session)
    
    if result.get('success'):
        await query.edit_message_text(
            f"✅ <b>Trade #{result['trade_id']} for {result['asset']} added to your portfolio!</b>\n\n"
            f"Use <code>/myportfolio</code> to view your open trades.",
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text(f"❌ Error: {result.get('error', 'Unknown error')}")
        
    context.user_data.pop('pending_trade', None)
    return ConversationHandler.END

async def handle_cancellation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the cancellation of the forwarding process."""
    query = update.callback_query
    await query.answer("Cancelled")
    context.user_data.pop('pending_trade', None)
    await query.edit_message_text("❌ Operation cancelled.")
    return ConversationHandler.END

def create_forwarding_conversation_handler():
    """Creates the ConversationHandler for the forwarding feature."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                forwarding_entry_point
            )
        ],
        states={
            AWAITING_CONFIRMATION: [
                CallbackQueryHandler(handle_confirmation, pattern="^confirm_forwarded_trade$"),
                CallbackQueryHandler(handle_cancellation, pattern="^cancel_forwarded_trade$")
            ]
        },
        fallbacks=[
            CallbackQueryHandler(handle_cancellation, pattern="^cancel_forwarded_trade$")
        ],
        name="forwarding_conversation",
        persistent=True,
        per_user=True,
        per_chat=True,
    )

#END