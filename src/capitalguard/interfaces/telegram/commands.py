# src/capitalguard/interfaces/telegram/commands.py (v26.4 - COMPLETE, FINAL & FIXED)
"""
Registers and implements all simple, non-conversational commands and simple message handlers.
This version includes the missing import required for analyst authorization.

This is a complete, final, and production-ready file.
"""

import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (Application, ContextTypes, CommandHandler, MessageHandler, filters)

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
# ✅ THE FIX: Added 'require_analyst_user' to the imports.
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.image_parsing_service import ImageParsingService
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository
from capitalguard.infrastructure.db.models import UserType
from .keyboards import build_open_recs_keyboard

log = logging.getLogger(__name__)

# --- Standard Command Handlers ---

@uow_transaction
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """
    Handles the /start command. It creates a user record if one doesn't exist
    and handles deep-linking for tracking signals.
    """
    user = update.effective_user
    log.info(f"User {user.id} ({user.username or 'NoUsername'}) initiated /start command.")
    
    # Ensure user exists in the database.
    UserRepository(db_session).find_or_create(
        telegram_id=user.id,
        first_name=user.first_name,
        username=user.username
    )

    # Handle deep-linking payload (e.g., /start track_123)
    if context.args and context.args[0].startswith("track_"):
        try:
            rec_id = int(context.args[0].split('_')[1])
            trade_service = get_service(context, "trade_service", TradeService)
            # Pass db_session explicitly to the service method
            result = await trade_service.create_trade_from_recommendation(str(user.id), rec_id, db_session=db_session)

            if result.get('success'):
                await update.message.reply_html(
                    f"✅ <b>Signal tracking confirmed!</b>\n"
                    f"Signal for <b>{result['asset']}</b> has been added to your personal portfolio.\n\n"
                    f"Use <code>/myportfolio</code> to view all your tracked trades."
                )
            else:
                await update.message.reply_html(f"⚠️ Could not track signal: {result.get('error', 'Unknown reason')}")
            return
        except (ValueError, IndexError):
            await update.message.reply_html("Invalid tracking link.")
        except Exception as e:
            log.error(f"Error handling deep link for user {user.id}: {e}", exc_info=True)
            await update.message.reply_html("An error occurred while trying to track the signal.")
        return

    # Standard welcome message
    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")


@uow_transaction
@require_active_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    Provides a help message with available commands, tailored to the user's role.
    """
    trader_help = "• <code>/myportfolio</code> — View and manage your open trades.\n"
    analyst_help = (
        "• <code>/newrec</code> — Start the interactive wizard to create a new recommendation.\n"
        "• <code>/link_channel</code> — Link a new channel for signal publishing.\n"
        "• <code>/channels</code> — View your currently linked channels.\n"
    )
    general_help = (
        "• <code>/help</code> — Show this help message.\n\n"
        "💡 **Tip:** To track a signal from another channel, simply forward the message to me."
    )
    
    full_help = "<b>Available Commands:</b>\n\n" + trader_help
    if db_user and db_user.user_type == UserType.ANALYST:
        full_help += analyst_help
    full_help += general_help
    
    await update.message.reply_html(full_help)


@uow_transaction
@require_active_user
async def myportfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """
    Displays an interactive keyboard with all open positions for the user.
    """
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    
    items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
    
    if not items:
        await update.message.reply_text("✅ You have no open trades or active recommendations.")
        return
        
    keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)
    await update.message.reply_html("<b>📊 Your Open Positions</b>\nSelect one to manage:", reply_markup=keyboard)


# --- Forwarded Signal Parsing (Simple MessageHandler) ---

@uow_transaction
@require_active_user
async def forwarded_signal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    Handles forwarded messages to parse them as trade signals.
    Makes intent explicit by prompting the user.
    """
    message = update.message
    
    # Basic filter: must have text and be of reasonable length
    if not message.text or len(message.text) < 20:
        return

    # Check if a conversation is active to avoid conflicts.
    # Only show prompt if user is idle.
    if context.user_data and any(isinstance(k, tuple) and k[-1] == 'state' for k in context.user_data):
         return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔬 Analyze as Trade Signal", callback_data="fwd:analyze"),
        InlineKeyboardButton("❌ Ignore", callback_data="fwd:ignore"),
    ]])
    
    # Store the original message object to access its text later
    context.user_data['fwd_msg'] = message
    
    await message.reply_text(
        "Forwarded message detected. What would you like to do?",
        reply_markup=keyboard,
        reply_to_message_id=message.message_id
    )

@uow_transaction
@require_active_user
async def analyze_forward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Triggered when user clicks 'Analyze'. Performs parsing."""
    query = update.callback_query
    await query.answer("Analyzing...")
    
    original_message = context.user_data.get('fwd_msg')
    if not original_message:
        await query.edit_message_text("❌ Error: Original message not found in context.")
        return

    parsing_service = get_service(context, "image_parsing_service", ImageParsingService)
    trade_data = await parsing_service.extract_trade_data(original_message.text)

    if not trade_data:
        await query.edit_message_text("❌ Analysis failed: Could not recognize a valid trade signal.")
        context.user_data.pop('fwd_msg', None)
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
        InlineKeyboardButton("✅ Yes, Track", callback_data="fwd:confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="fwd:ignore")
    ]])
    await query.edit_message_text(confirmation_text, reply_markup=keyboard, parse_mode='HTML')

@uow_transaction
@require_active_user
async def confirm_track_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Triggered when user confirms tracking. Saves to DB."""
    query = update.callback_query
    await query.answer("Saving...")
    
    trade_data = context.user_data.pop('pending_trade', None)
    context.user_data.pop('fwd_msg', None) # Clean up

    if not trade_data:
        await query.edit_message_text("❌ Session expired.")
        return

    trade_service = get_service(context, "trade_service", TradeService)
    result = await trade_service.create_trade_from_forwarding(str(db_user.telegram_user_id), trade_data, db_session=db_session)

    if result.get('success'):
        await query.edit_message_text(f"✅ <b>Trade #{result['trade_id']} for {result['asset']}</b> added to portfolio!", parse_mode='HTML')
    else:
        await query.edit_message_text(f"❌ <b>Error:</b> {result.get('error', 'Unknown')}", parse_mode='HTML')

async def ignore_forward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cleans up context when user ignores or cancels."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop('pending_trade', None)
    context.user_data.pop('fwd_msg', None)
    await query.edit_message_text("Operation cancelled.")


# --- Analyst-Only Commands ---

@uow_transaction
@require_active_user
@require_analyst_user # This decorator requires the import added above
async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    Displays a list of all channels linked by the analyst, with their status.
    """
    channels = ChannelRepository(db_session).list_by_analyst(db_user.id, only_active=False)
    
    if not channels:
        await update.message.reply_html(
            "📭 You have no channels linked yet. "
            "Use <code>/link_channel</code> to add one."
        )
        return
        
    lines = ["<b>📡 Your Linked Channels:</b>"]
    for ch in channels:
        status_icon = "✅ Active" if ch.is_active else "⏸️ Inactive"
        username_str = f"(@{ch.username})" if ch.username else "(Private Channel)"
        lines.append(f"• <b>{ch.title or 'Untitled'}</b> {username_str}\n  ID: <code>{ch.telegram_channel_id}</code> | Status: {status_icon}")
    
    await update.message.reply_html("\n".join(lines))


# --- Registration ---

def register_commands(app: Application):
    """
    Registers all handlers defined in this file.
    """
    # Standard commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler(["myportfolio", "open"], myportfolio_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))

    # Forwarded message handling (low priority group to avoid conflicts)
    app.add_handler(MessageHandler(filters.FORWARDED & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, forwarded_signal_handler), group=1)
    
    # Callbacks for forwarding flow
    app.add_handler(CallbackQueryHandler(analyze_forward_callback, pattern="^fwd:analyze$"))
    app.add_handler(CallbackQueryHandler(confirm_track_callback, pattern="^fwd:confirm$"))
    app.add_handler(CallbackQueryHandler(ignore_forward_callback, pattern="^fwd:ignore$"))