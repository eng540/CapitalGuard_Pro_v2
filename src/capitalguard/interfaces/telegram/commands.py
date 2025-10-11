# src/capitalguard/interfaces/telegram/commands.py (v26.6 - COMPLETE, FINAL & ARCHITECTURALLY-CORRECT)
"""
Registers and implements all simple, non-conversational commands for the bot.
This version has been cleaned and refactored to only contain simple command logic,
adhering to the principle of single responsibility.
"""

import logging

from telegram import Update
from telegram.ext import (Application, ContextTypes, CommandHandler)

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
# ✅ Correctly importing all necessary decorators
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository
from capitalguard.infrastructure.db.models import UserType
from .keyboards import build_open_recs_keyboard

log = logging.getLogger(__name__)

# --- Command Handlers ---

@uow_transaction
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """
    Handles the /start command. It ensures a user is created and active in the
    database, then greets them. It also handles deep-linking for tracking signals.
    """
    user = update.effective_user
    log.info(f"User {user.id} ({user.username or 'NoUsername'}) initiated /start command.")

    # ✅ THE FIX: Call the enhanced find_or_create to ensure the user is registered
    # and active in the database from their very first interaction. This prevents
    # downstream errors where a user object might be None.
    db_user = UserRepository(db_session).find_or_create(
        telegram_id=user.id,
        first_name=user.first_name,
        username=user.username
    )

    # Deep-linking logic for tracking a recommendation via a "start?track_ID" link
    if context.args and context.args[0].startswith("track_"):
        try:
            rec_id = int(context.args[0].split('_')[1])
            trade_service = get_service(context, "trade_service", TradeService)
            # We now pass the confirmed db_user object, which is guaranteed to exist.
            result = await trade_service.create_trade_from_recommendation(str(db_user.telegram_user_id), rec_id, db_session=db_session)

            if result.get('success'):
                await update.message.reply_html(f"✅ <b>Signal tracking confirmed!</b>\nSignal for <b>{result['asset']}</b> has been added to your portfolio.\n\nUse <code>/myportfolio</code> to view your trades.")
            else:
                await update.message.reply_html(f"⚠️ Could not track signal: {result.get('error', 'Unknown')}")
            return # End execution after handling deep link
        except (ValueError, IndexError):
            await update.message.reply_html("Invalid tracking link.")
            return
        except Exception as e:
            log.error(f"Error handling deep link for user {user.id}: {e}", exc_info=True)
            await update.message.reply_html("An error occurred while processing the tracking link.")
            return

    await update.message.reply_html(f"👋 Welcome, {user.first_name}! You are now registered.\nUse /help for assistance.")

@uow_transaction
@require_active_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    trader_help = "• <code>/myportfolio</code> — View and manage your open trades.\n"
    analyst_help = (
        "• <code>/newrec</code> — Create a new recommendation.\n"
        "• <code>/link_channel</code> — Link a new channel for publishing.\n"
        "• <code>/channels</code> — View your linked channels.\n"
    )
    general_help = (
        "• <code>/help</code> — Show this help message.\n\n"
        "💡 **Tip:** To track a signal, simply forward the message to me."
    )
    full_help = "<b>Available Commands:</b>\n\n" + trader_help
    if db_user and db_user.user_type == UserType.ANALYST:
        full_help += analyst_help
    full_help += general_help
    await update.message.reply_html(full_help)

@uow_transaction
@require_active_user
async def myportfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    items = trade_service.get_open_positions_for_user(db_session, str(update.effective_user.id))
    if not items:
        await update.message.reply_text("✅ You have no open trades.")
        return
    keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)
    await update.message.reply_html("<b>📊 Your Open Positions</b>\nSelect one to manage:", reply_markup=keyboard)

@uow_transaction
@require_active_user
@require_analyst_user
async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    channels = ChannelRepository(db_session).list_by_analyst(db_user.id, only_active=False)
    if not channels:
        await update.message.reply_html("📭 You have no channels linked. Use <code>/link_channel</code> to add one.")
        return
    lines = ["<b>📡 Your Linked Channels:</b>"]
    for ch in channels:
        status_icon = "✅ Active" if ch.is_active else "⏸️ Inactive"
        username_str = f"(@{ch.username})" if ch.username else "(Private Channel)"
        lines.append(f"• <b>{ch.title or 'Untitled'}</b> {username_str}\n  ID: <code>{ch.telegram_channel_id}</code> | Status: {status_icon}")
    await update.message.reply_html("\n".join(lines))


# --- Registration ---

def register_commands(app: Application):
    """Registers all simple command handlers defined in this file."""
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler(["myportfolio", "open"], myportfolio_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))