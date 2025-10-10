# src/capitalguard/interfaces/telegram/forward_parsing_handler.py (v1.0 - COMPLETE, FINAL & PRODUCTION-READY)
"""
Handles the user flow for parsing a trade signal from a forwarded message.

This feature is designed to be explicit to avoid conflicts. It works as follows:
1. A simple MessageHandler catches any forwarded text message.
2. Instead of parsing immediately, it replies with a button asking for user intent.
3. A CallbackQueryHandler triggers the actual parsing via ImageParsingService.
4. Further callbacks handle the confirmation or cancellation of saving the parsed trade.
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

# --- Primary Handler: Catching the Forwarded Message ---

async def forwarded_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Catches any forwarded text message and prompts the user for action,
    rather than parsing automatically. This prevents accidental triggers.
    """
    message = update.message
    # Basic filter to ignore very short or irrelevant forwarded messages.
    if not message.text or len(message.text) < 20:
        return

    # To prevent this handler from interfering with other conversations that might
    # expect a forwarded message (like channel linking), we check if any other
    # conversation is active for this user.
    if context.user_data and any(isinstance(k, tuple) and k[-1] == 'state' for k in context.user_data):
         log.debug("Forwarded message ignored because a conversation is active.")
         return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔬 Analyze as Trade Signal", callback_data="fwd_parse:analyze"),
        InlineKeyboardButton("❌ Ignore", callback_data="fwd_parse:ignore"),
    ]])
    
    await message.reply_text(
        "Forwarded message detected. What would you like to do?",
        reply_markup=keyboard
    )

# --- Callback Handlers: Processing User Intent ---

@uow_transaction
@require_active_user
async def parse_forwarded_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """
    Triggered when the user clicks 'Analyze'. This function performs the actual parsing.
    """
    query = update.callback_query
    await query.answer("Analyzing...")

    original_message = query.message.reply_to_message
    if not original_message or not original_message.text:
        await query.edit_message_text("❌ Error: The original message could not be found.")
        return

    parsing_service = get_service(context, "image_parsing_service", ImageParsingService)
    trade_data = await parsing_service.extract_trade_data(original_message.text)

    if not trade_data:
        await query.edit_message_text("❌ Analysis failed: Could not recognize a valid trade signal in the message.")
        return

    context.user_data['pending_trade'] = trade_data
    asset, side = trade_data['asset'], trade_data['side']
    side_emoji = "📈" if side == "LONG" else "📉"
    
    confirmation_text = (
        f"{side_emoji} <b>Signal Parsed</b>\n\n"
        f"<b>Asset:</b> {asset}\n"
        f"<b>Direction:</b> {side}\n"
        f"<b>Entry:</b> {trade_data['entry']:g}\n"
        f"<b>Stop Loss:</b> {trade_data['stop_loss']:g}\n\n"
        f"Do you want to add this to your personal portfolio?"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, Confirm & Track", callback_data="fwd_parse:confirm"),
        InlineKeyboardButton("❌ No, Cancel", callback_data="fwd_parse:cancel")
    ]])
    await query.edit_message_text(confirmation_text, reply_markup=keyboard, parse_mode='HTML')


@uow_transaction
@require_active_user
async def confirm_parsed_trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Triggered when the user confirms the parsed data. Saves the trade."""
    query = update.callback_query
    await query.answer("Saving...")
    
    trade_data = context.user_data.pop('pending_trade', None)
    if not trade_data:
        await query.edit_message_text("❌ Session expired. Please forward the message again.")
        return

    trade_service = get_service(context, "trade_service", TradeService)
    result = await trade_service.create_trade_from_forwarding(str(db_user.telegram_user_id), trade_data, db_session=db_session)

    if result.get('success'):
        await query.edit_message_text(f"✅ <b>Trade #{result['trade_id']} for {result['asset']}</b> has been added to your portfolio!", parse_mode='HTML')
    else:
        await query.edit_message_text(f"❌ <b>Error:</b> {result.get('error', 'Unknown')}", parse_mode='HTML')


async def ignore_or_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cleans up the prompt message when the user ignores or cancels."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop('pending_trade', None)
    await query.edit_message_text("Operation cancelled.")


# --- Registration ---

def register_forward_parsing_handlers(app: Application):
    """Registers all handlers related to the forward-parsing feature."""
    # The handler for catching the initial forward has a low priority (group=1)
    # to ensure it doesn't interfere with active conversations.
    app.add_handler(MessageHandler(filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, forwarded_message_handler), group=1)
    
    # Callback handlers for the buttons.
    app.add_handler(CallbackQueryHandler(parse_forwarded_callback, pattern="^fwd_parse:analyze$"))
    app.add_handler(CallbackQueryHandler(confirm_parsed_trade_callback, pattern="^fwd_parse:confirm$"))
    app.add_handler(CallbackQueryHandler(ignore_or_cancel_callback, pattern=r"^fwd_parse:(ignore|cancel)$"))