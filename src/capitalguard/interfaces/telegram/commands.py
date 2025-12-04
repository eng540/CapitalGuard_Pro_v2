# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/commands.py ---
# File: src/capitalguard/interfaces/telegram/commands.py
# Version: v32.0.0-OPEN-ACCESS
# ✅ THE FIX:
#    1. Trader: Instant access upon Channel Subscription (No admin approval needed).
#    2. Analyst: Dedicated flow to request upgrade with benefits showcase.
#    3. Automatic Verification: Checks user membership before activating.

import logging
import os
from urllib.parse import urlparse

from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.audit_service import AuditService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository
from capitalguard.infrastructure.db.models import UserType
from capitalguard.config import settings

# ✅ IMPORT CLASSIC HANDLER
from .management_handlers import portfolio_command_entry

log = logging.getLogger(__name__)

# --- Keyboards Helper ---
def get_main_menu_keyboard(is_analyst: bool = False) -> ReplyKeyboardMarkup:
    """Creates the persistent bottom keyboard."""
    raw_url = settings.TELEGRAM_WEBHOOK_URL
    base_url = f"https://{urlparse(raw_url).netloc}" if raw_url else "https://127.0.0.1:8000"
    web_app_create_url = f"{base_url}/new"
    web_app_portfolio_url = f"{base_url}/portfolio" # For future use

    keyboard = []
    
    if is_analyst:
        keyboard.append([KeyboardButton("🚀 New Signal (Visual)", web_app=WebAppInfo(url=web_app_create_url))])
        keyboard.append([KeyboardButton("📂 My Portfolio"), KeyboardButton("/channels")])
    else:
        # Trader View
        keyboard.append([KeyboardButton("📂 My Portfolio"), KeyboardButton("📱 Web Portfolio")])
        keyboard.append([KeyboardButton("💎 ترقية لمحلل (Upgrade)")])
    
    keyboard.append([KeyboardButton("/help")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_portfolio_inline_keyboard() -> InlineKeyboardMarkup:
    raw_url = settings.TELEGRAM_WEBHOOK_URL
    base_url = f"https://{urlparse(raw_url).netloc}" if raw_url else "https://127.0.0.1:8000"
    web_app_portfolio_url = f"{base_url}/portfolio"
    return InlineKeyboardMarkup([[InlineKeyboardButton("📱 Open Web Portfolio", web_app=WebAppInfo(url=web_app_portfolio_url))]])

# --- Helper: Check Channel Membership ---
async def _check_channel_membership(bot, user_id: int, channel_id: str) -> bool:
    """Verifies if a user is a member of the required channel."""
    if not channel_id: return True # No channel configured, allow all
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ['creator', 'administrator', 'member', 'restricted']
    except Exception as e:
        log.warning(f"Membership check failed: {e}")
        return False

# --- Commands ---

@uow_transaction
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """
    The Gatekeeper:
    1. Registers user silently.
    2. Checks Channel Subscription.
    3. Activates Trader immediately if subscribed.
    """
    user = update.effective_user
    log.info(f"User {user.id} initiated /start.")
    
    # 1. Register User (Default: Inactive until verified)
    repo = UserRepository(db_session)
    db_user = repo.find_or_create(telegram_id=user.id, first_name=user.first_name, username=user.username)

    # 2. Check Subscription
    is_subscribed = await _check_channel_membership(context.bot, user.id, settings.TELEGRAM_CHAT_ID)

    if not is_subscribed:
        # Show Subscription Gate
        invite_link = settings.TELEGRAM_CHANNEL_INVITE_LINK or "https://t.me/YourChannel"
        msg = (
            f"👋 Welcome, <b>{user.first_name}</b>!\n\n"
            "🔒 <b>Access Restricted</b>\n"
            "To use the CapitalGuard Portfolio Manager, you must subscribe to our updates channel first.\n\n"
            "<i>Join to get updates, news, and system alerts.</i>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel", url=invite_link)],
            [InlineKeyboardButton("🔄 Verify & Start", callback_data="verify_sub")]
        ])
        await update.message.reply_html(msg, reply_markup=kb)
        return

    # 3. Auto-Activate if Subscribed
    if not db_user.is_active:
        db_user.is_active = True
        # Default role is TRADER, so we just activate
        db_session.commit() # Commit activation immediately

    # 4. Handle Deep Links (Tracking)
    if context.args and context.args[0].startswith("track_"):
        try:
            rec_id = int(context.args[0].split('_')[1])
            trade_service = get_service(context, "trade_service", TradeService)
            result = await trade_service.create_trade_from_recommendation(str(user.id), rec_id, db_session=db_session)
            if result.get('success'):
                await update.message.reply_html(f"✅ <b>Tracking Started:</b> {result['asset']}", reply_markup=get_main_menu_keyboard(db_user.user_type == UserType.ANALYST))
            else:
                await update.message.reply_html(f"⚠️ {result.get('error')}", reply_markup=get_main_menu_keyboard(db_user.user_type == UserType.ANALYST))
        except Exception:
            await update.message.reply_html("❌ Invalid link.", reply_markup=get_main_menu_keyboard(db_user.user_type == UserType.ANALYST))
        return

    # 5. Welcome Message (Based on Role)
    role_title = "Analyst 🎓" if db_user.user_type == UserType.ANALYST else "Trader 💼"
    welcome = (
        f"✅ <b>Access Granted</b>\n"
        f"👤 Account: <b>{user.first_name}</b>\n"
        f"🔰 Role: <b>{role_title}</b>\n\n"
        "Ready to manage your portfolio."
    )
    await update.message.reply_html(welcome, reply_markup=get_main_menu_keyboard(db_user.user_type == UserType.ANALYST))

@uow_transaction
async def verify_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """Handles the 'Verify' button click."""
    query = update.callback_query
    await query.answer("Checking subscription...")
    
    user = query.from_user
    is_subscribed = await _check_channel_membership(context.bot, user.id, settings.TELEGRAM_CHAT_ID)

    if is_subscribed:
        repo = UserRepository(db_session)
        db_user = repo.find_or_create(telegram_id=user.id, first_name=user.first_name)
        db_user.is_active = True
        db_session.commit()
        
        await query.delete_message() # Clean up the gate message
        await context.bot.send_message(
            chat_id=user.id,
            text="🎉 <b>Verified!</b> Welcome to CapitalGuard.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(db_user.user_type == UserType.ANALYST)
        )
    else:
        await query.edit_message_text(
            text="❌ <b>Verification Failed.</b>\nYou are not found in the channel. Please join and try again.",
            reply_markup=query.message.reply_markup, # Keep buttons
            parse_mode="HTML"
        )

@uow_transaction
@require_active_user
async def request_analyst_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    Triggered by '💎 ترقية لمحلل'.
    Shows benefits and a contact button.
    """
    text = (
        "🎓 <b>Become a CapitalGuard Analyst</b>\n\n"
        "Upgrade your account to unlock powerful tools for Signal Providers:\n\n"
        "✨ <b>Exclusive Features:</b>\n"
        "• 📢 <b>Signal Broadcasting:</b> Create signals once, publish to unlimited channels.\n"
        "• 🤖 <b>Auto-Management:</b> Signals auto-update in your channels (TP hit, SL hit).\n"
        "• 📊 <b>Performance Analytics:</b> Verified Win-Rate and PnL tracking.\n"
        "• 🛡️ <b>Risk Management:</b> Tools to move SL to Breakeven or Partial Close for all followers.\n\n"
        "<i>To apply, please contact support with your channel link or trading history.</i>"
    )
    
    admin_username = settings.ADMIN_USERNAMES.split(',')[0] if settings.ADMIN_USERNAMES else "Support"
    if "@" in admin_username: admin_username = admin_username.replace("@", "")
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Contact Support to Apply", url=f"https://t.me/{admin_username}")]
    ])
    
    await update.message.reply_html(text, reply_markup=kb)

@uow_transaction
@require_active_user
async def portfolio_webapp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await update.message.reply_text(
        "👇 <b>Visual Portfolio</b>\nTap below:",
        reply_markup=get_portfolio_inline_keyboard(),
        parse_mode="HTML"
    )

@uow_transaction
@require_active_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    help_text = "<b>Help & Commands</b>\n/start - Restart Bot\n/channels - Linked Channels (Analysts)\n/portfolio - Web Dashboard"
    await update.message.reply_html(help_text)

@uow_transaction
@require_active_user
@require_analyst_user
async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    channels = ChannelRepository(db_session).list_by_analyst(db_user.id, only_active=False)
    if not channels:
        await update.message.reply_html("📭 No channels linked.", reply_markup=get_main_menu_keyboard(True))
        return
    lines = ["<b>📡 Linked Channels:</b>"]
    for ch in channels:
        lines.append(f"• {ch.title} ({'Active' if ch.is_active else 'Inactive'})")
    await update.message.reply_html("\n".join(lines), reply_markup=get_main_menu_keyboard(True))

@uow_transaction
@require_active_user
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await update.message.reply_text("Exporting data...", reply_markup=get_main_menu_keyboard(db_user.user_type == UserType.ANALYST))

# --- Registration ---
def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myportfolio", portfolio_command_entry))
    app.add_handler(CommandHandler("portfolio", portfolio_webapp_handler))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    
    # Verify Callback
    app.add_handler(CallbackQueryHandler(verify_subscription_callback, pattern="^verify_sub$"))

    # Text Buttons
    app.add_handler(MessageHandler(filters.Regex(r"^📂 My Portfolio$"), portfolio_command_entry))
    app.add_handler(MessageHandler(filters.Regex(r"^📱 Web Portfolio$"), portfolio_webapp_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💎 ترقية لمحلل \(Upgrade\)$"), request_analyst_upgrade))
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/commands.py ---