# src/capitalguard/interfaces/telegram/commands.py (v25.6 - FINAL DECORATOR FIX)
"""
Registers and implements all non-conversational commands for the Telegram bot.
"""

import logging
from typing import Optional, Tuple

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository
from capitalguard.infrastructure.db.models import UserType
from .keyboards import build_open_recs_keyboard

log = logging.getLogger(__name__)

AWAITING_FORWARD_KEY = "awaiting_forward_channel_link"

def _extract_forwarded_channel(message: Update.message) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Safely extracts channel info from a forwarded message."""
    chat_obj = getattr(message, "forward_from_chat", None)
    if chat_obj and getattr(chat_obj, "type", None) == "channel":
        return (int(chat_obj.id), chat_obj.title, chat_obj.username)
    return None, None, None

async def _bot_has_post_rights(context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> bool:
    """Checks if the bot can post messages to a channel."""
    try:
        sent_message = await context.bot.send_message(chat_id=channel_id, text="✅ Channel link verification successful.")
        await context.bot.delete_message(chat_id=channel_id, message_id=sent_message.message_id)
        return True
    except Exception as e:
        log.warning("Bot posting rights check failed for channel %s: %s", channel_id, e)
        return False

@uow_transaction
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """
    Handles the /start command, including user creation and deep linking for tracking signals.
    """
    user = update.effective_user
    log.info(f"User {user.id} ({user.username}) initiated /start command.")
    
    UserRepository(db_session).find_or_create(
        telegram_id=user.id, first_name=user.first_name, username=user.username
    )

    if context.args and context.args[0].startswith("track_"):
        try:
            rec_id = int(context.args[0].split('_')[1])
            trade_service = get_service(context, "trade_service", TradeService)
            result = await trade_service.create_trade_from_recommendation(str(user.id), rec_id, db_session=db_session)

            if result.get('success'):
                await update.message.reply_html(
                    f"✅ <b>Signal #{result['asset']} has been added to your portfolio!</b>\n\n"
                    f"Use <code>/myportfolio</code> to view your tracked trades."
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

    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")

@uow_transaction
@require_active_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Provides a help message tailored to the user's role."""
    trader_help = (
        "<b>--- Trading ---</b>\n"
        "• <code>/myportfolio</code> — View your open trades.\n"
        "• Forward any signal message to me to start tracking it!\n\n"
    )
    analyst_help = (
        "<b>--- Analyst Features ---</b>\n"
        "• <code>/newrec</code> — Create a new recommendation.\n"
        "• <code>/channels</code> — View & manage your linked channels.\n"
        "• <code>/link_channel</code> — Link a new channel.\n\n"
    )
    general_help = (
        "<b>--- General ---</b>\n"
        "• <code>/help</code> — Show this help message."
    )
    
    full_help = "<b>Available Commands:</b>\n\n" + trader_help
    if db_user and db_user.user_type == UserType.ANALYST:
        full_help += analyst_help
    full_help += general_help
    
    await update.message.reply_html(full_help)

@uow_transaction
@require_active_user
async def myportfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Displays all open positions for the user."""
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    user_telegram_id = str(update.effective_user.id)
    
    items = trade_service.get_open_positions_for_user(db_session, user_telegram_id)
    
    if not items:
        await update.message.reply_text("✅ You have no open trades or recommendations.")
        return
        
    keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)
    await update.message.reply_html("<b>📊 Your Open Positions</b>\nSelect one to manage:", reply_markup=keyboard)

@uow_transaction
@require_active_user
@require_analyst_user
async def link_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
    """Initiates the channel linking process for an analyst."""
    context.user_data[AWAITING_FORWARD_KEY] = True
    await update.message.reply_html("<b>🔗 Link a Channel</b>\nPlease forward a message from the target channel to this chat.")

@uow_transaction
@require_active_user
async def forwarded_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles forwarded messages for either channel linking or trade tracking."""
    if context.user_data.pop(AWAITING_FORWARD_KEY, False):
        msg = update.message
        user_tg_id = update.effective_user.id
        chat_id, title, username = _extract_forwarded_channel(msg)
        
        if not chat_id:
            await msg.reply_text("❌ This does not appear to be a message from a channel. Please try again.")
            return

        await msg.reply_text(f"⏳ Verifying posting rights in channel '{title}' (ID: {chat_id})...")
        
        if not await _bot_has_post_rights(context, chat_id):
            await msg.reply_text("❌ Could not post in the channel. Please ensure the bot is an administrator with posting rights.")
            return

        user = UserRepository(db_session).find_by_telegram_id(user_tg_id)
        ChannelRepository(db_session).add(analyst_id=user.id, telegram_channel_id=chat_id, username=username, title=title)
        
        uname_disp = f"@{username}" if username else "a private channel"
        await msg.reply_html(f"✅ Channel successfully linked: <b>{title or '-'}</b> ({uname_disp})\nID: <code>{chat_id}</code>")
        return
    pass

@uow_transaction
@require_active_user
@require_analyst_user
async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Displays a list of all channels linked by the analyst."""
    channels = ChannelRepository(db_session).list_by_analyst(db_user.id, only_active=False)
    
    if not channels:
        await update.message.reply_text("📭 You have no channels linked yet. Use /link_channel to add one.")
        return
        
    lines = ["<b>📡 Your Linked Channels</b>"]
    for ch in channels:
        uname = f"@{ch.username}" if ch.username else "—"
        title = ch.title or "—"
        status = "✅ Active" if ch.is_active else "⏸️ Inactive"
        lines.append(f"• <b>{title}</b> ({uname} / <code>{ch.telegram_channel_id}</code>) — {status}")
    
    await update.message.reply_html("\n".join(lines))

def register_commands(app: Application):
    """Registers all command and message handlers defined in this file."""
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler(["myportfolio", "open"], myportfolio_cmd))
    app.add_handler(CommandHandler("link_channel", link_channel_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(MessageHandler(filters.FORWARDED & ~filters.COMMAND & filters.ChatType.PRIVATE, forwarded_message_handler), group=0)

#END