# src/capitalguard/interfaces/telegram/commands.py (v3.1 - Channel Commands Restored)
import logging
from typing import Optional, Tuple

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

from .helpers import get_service, unit_of_work
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.audit_service import AuditService
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

log = logging.getLogger(__name__)

AWAITING_FORWARD_KEY = "awaiting_forward_channel_link"

def _extract_forwarded_channel(message) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    chat_obj = getattr(message, "forward_from_chat", None)
    if chat_obj and getattr(chat_obj, "type", None) == "channel":
        return (int(chat_obj.id), chat_obj.title, chat_obj.username)
    return None, None, None

async def _bot_has_post_rights(context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> bool:
    try:
        # Send a silent message to verify rights without disturbing the channel
        await context.bot.send_message(chat_id=channel_id, text=".", disable_notification=True)
        return True
    except Exception as e:
        log.warning("Bot posting rights check failed for channel %s: %s", channel_id, e)
        return False

@unit_of_work
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    user = update.effective_user
    log.info(f"User {user.id} ({user.username}) started interaction.")
    UserRepository(db_session).find_or_create(telegram_id=user.id, first_name=user.first_name, username=user.username)
    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")

@require_active_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check user type to show appropriate help
    user_repo = UserRepository(SessionLocal())
    db_user = user_repo.find_by_telegram_id(update.effective_user.id)
    
    trader_help = (
        "<b>--- Trading ---</b>\n"
        "• <code>/myportfolio</code> — View your open trades.\n"
        "• Forward any signal to me to start tracking!\n\n"
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
    if db_user and db_user.user_type.name == 'ANALYST':
        full_help += analyst_help
    full_help += general_help
    
    await update.message.reply_html(full_help)

@require_active_user
@unit_of_work
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    trade_service = get_service(context, "trade_service", TradeService)
    price_service = get_service(context, "price_service", PriceService)
    user_telegram_id = str(update.effective_user.id)
    
    items = trade_service.get_open_positions_for_user(db_session, user_telegram_id)
    
    if not items:
        await update.message.reply_text("✅ You have no open trades or recommendations.")
        return
        
    keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)
    await update.message.reply_html("<b>📊 Your Open Positions</b>\nSelect one to manage:", reply_markup=keyboard)

@require_active_user
@require_analyst_user
async def link_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[AWAITING_FORWARD_KEY] = True
    await update.message.reply_html("<b>🔗 Link a Channel via Forwarding</b>\n"
                                    "Please forward <u>any message</u> from the target channel to this chat.\n"
                                    "• This supports both <b>private</b> and <b>public</b> channels.\n"
                                    "• Ensure this bot is an administrator with posting permissions in the channel.")

@require_active_user
@require_analyst_user
@unit_of_work
async def link_channel_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    msg = update.message
    if not context.user_data.pop(AWAITING_FORWARD_KEY, False):
        # This forward is not for linking a channel, so ignore it in this handler
        return

    user_tg_id = update.effective_user.id
    chat_id, title, username = _extract_forwarded_channel(msg)
    
    if not chat_id:
        await msg.reply_text("❌ This does not seem to be a message from a channel. Please try again.")
        return

    await msg.reply_text(f"⏳ Verifying posting rights in channel '{title}' (ID: {chat_id})...")
    
    if not await _bot_has_post_rights(context, chat_id):
        await msg.reply_text("❌ Could not post in the channel. Please ensure the bot is an administrator with posting rights and try again.")
        return

    user = UserRepository(db_session).find_by_telegram_id(user_tg_id)
    ChannelRepository(db_session).add(owner_user_id=user.id, telegram_channel_id=chat_id, username=username, title=title)
    
    uname_disp = f"@{username}" if username else "a private channel"
    await msg.reply_html(f"✅ Channel successfully linked: <b>{title or '-'}</b> ({uname_disp})\nID: <code>{chat_id}</code>")

@require_active_user
@require_analyst_user
@unit_of_work
async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    user_tg_id = update.effective_user.id
    user = UserRepository(db_session).find_by_telegram_id(user_tg_id)
    channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False) if user else []
    if not channels:
        await update.message.reply_text("📭 You have no channels linked yet. Use /link_channel to add one.")
        return
    lines = ["<b>📡 Your Linked Channels</b>"]
    for ch in channels:
        uname = f"@{ch.username}" if ch.username else "—"
        title = ch.title or "—"
        status = "✅ Active" if ch.is_active else "⏸️ Inactive"
        lines.append(f"• <b>{title}</b> ({uname} / <code>{ch.telegram_channel_id}</code>) — {status}")
    lines.append("\nℹ️ To manage: <code>/toggle_channel &lt;id&gt;</code>")
    await update.message.reply_html("\n".join(lines))

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler(["myportfolio", "open"], open_cmd))
    
    # Analyst-specific commands
    app.add_handler(CommandHandler("link_channel", link_channel_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))
    
    # Handler for the channel linking process
    app.add_handler(MessageHandler(filters.FORWARDED & ~filters.COMMAND, link_channel_forward_handler))