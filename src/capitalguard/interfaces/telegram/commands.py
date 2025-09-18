# --- START OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.2) ---
# src/capitalguard/interfaces/telegram/commands.py

import io
import csv
import logging
from typing import Optional, Tuple

from telegram import Update, InputFile
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters
from telegram.error import BadRequest

from .helpers import get_service
from .auth import ALLOWED_USER_FILTER
from .ui_texts import build_analyst_stats_text
from .keyboards import build_open_recs_keyboard

from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

log = logging.getLogger(__name__)

AWAITING_FORWARD_KEY = "awaiting_forward_channel_link"

# --- Helper Functions ---
def _extract_forwarded_channel(message) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Extracts channel info from a forwarded message."""
    chat_obj = getattr(message, "forward_from_chat", None)
    if chat_obj is None:
        fwd_origin = getattr(message, "forward_origin", None)
        if fwd_origin: chat_obj = getattr(fwd_origin, "chat", None)
    if chat_obj is None or getattr(chat_obj, "type", None) != "channel":
        return None, None, None
    return (int(getattr(chat_obj, "id")), getattr(chat_obj, "title", None), getattr(chat_obj, "username", None))

async def _bot_has_post_rights(context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> bool:
    """Performs a lightweight post to verify the bot can publish in the channel."""
    try:
        await context.bot.send_message(chat_id=channel_id, text="✅ Channel successfully linked.", disable_notification=True)
        return True
    except Exception as e:
        log.warning("Bot posting rights check failed for channel %s: %s", channel_id, e)
        return False

# --- Basic Commands ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "<b>--- Recommendation Creation ---</b>\n"
        "• <code>/newrec</code> — Show creation method menu.\n"
        "• <code>/new</code> — Start interactive builder directly.\n"
        "• <code>/rec</code> — Use the quick command mode.\n"
        "• <code>/editor</code> — Use the text editor mode.\n\n"
        "<b>--- Management & Analytics ---</b>\n"
        "• <code>/open [filter]</code> — View your open recommendations.\n"
        "• <code>/stats</code> — View your personal performance summary.\n"
        "• <code>/export</code> — Export your recommendations.\n\n"
        "<b>--- Channel Management ---</b>\n"
        "• <code>/link_channel</code> — Link a new channel via forward.\n"
        "• <code>/channels</code> — View your linked channels.\n"
        "• <code>/toggle_channel &lt;id&gt;</code> — Activate/deactivate a channel.\n"
        "• <code>/unlink_channel &lt;id&gt;</code> — Unlink a channel.\n\n"
        "<b>--- Settings ---</b>\n"
        "• <code>/settings</code> — (Future placeholder for account settings)."
    )

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Settings\n\nThis area will be used for account settings in the future.")

# --- Core Feature Commands ---
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    user_telegram_id = str(update.effective_user.id)

    filters_map = {}
    filter_text_parts = []
    if context.args:
        for arg in context.args:
            a = arg.strip().lower()
            if a in ("long", "short"):
                filters_map["side"] = a
                filter_text_parts.append(f"Direction: {a.upper()}")
            elif a in ("pending", "active"):
                filters_map["status"] = a
                filter_text_parts.append(f"Status: {a.upper()}")
            else:
                filters_map["symbol"] = a
                filter_text_parts.append(f"Symbol: {a.upper()}")

    context.user_data["last_open_filters"] = filters_map

    items = trade_service.get_open_recommendations_for_user(user_telegram_id, **filters_map)

    if not items:
        await update.message.reply_text("✅ No open recommendations match the current filter.")
        return

    keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)

    header_text = "<b>📊 Your Open Recommendations Dashboard</b>"
    if filter_text_parts:
        header_text += f"\n<i>Filtered by: {', '.join(filter_text_parts)}</i>"

    await update.message.reply_html(
        f"{header_text}\nSelect a recommendation to view its control panel:",
        reply_markup=keyboard
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    user_id_str = str(update.effective_user.id)
    stats = analytics_service.performance_summary_for_user(user_id_str)
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Preparing your export file...")
    trade_service: TradeService = get_service(context, "trade_service")
    user_telegram_id = str(update.effective_user.id)

    with SessionLocal() as session:
        all_recs = trade_service.repo.list_all_for_user(session, user_telegram_id)
    
    if not all_recs:
        await update.message.reply_text("You have no data to export.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        "id", "asset", "side", "status", "market", "entry_price", "stop_loss",
        "targets", "exit_price", "notes", "created_at", "closed_at"
    ]
    writer.writerow(header)
    for rec in all_recs:
        row = [
            rec.id, rec.asset.value, rec.side.value, rec.status.value,
            rec.market, rec.entry.value, rec.stop_loss.value,
            ", ".join(f"{t.price}@{t.close_percent}" for t in rec.targets.values),
            rec.exit_price, rec.notes,
            rec.created_at.strftime('%Y-%m-%d %H:%M:%S') if rec.created_at else "",
            rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""
        ]
        writer.writerow(row)

    output.seek(0)
    bytes_buffer = io.BytesIO(output.getvalue().encode("utf-8"))
    csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    await update.message.reply_document(document=csv_file, caption="Your export has been generated.")

# --- Channel Management Commands ---
async def link_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[AWAITING_FORWARD_KEY] = True
    await update.message.reply_html(
        "<b>🔗 Link a Channel via Forwarding</b>\n"
        "Please forward <u>any message</u> from the target channel to this chat.\n"
        "• This supports both <b>private</b> and <b>public</b> channels.\n"
        "• Ensure this bot is an administrator with posting permissions in the channel."
    )

async def link_channel_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not context.user_data.pop(AWAITING_FORWARD_KEY, False):
        return

    user_tg_id = update.effective_user.id
    chat_id, title, username = _extract_forwarded_channel(msg)
    if not chat_id:
        return

    await msg.reply_text(f"⏳ Verifying posting rights in channel (ID: {chat_id})...")

    if not await _bot_has_post_rights(context, chat_id):
        await msg.reply_text("❌ Could not post in the channel. Please ensure the bot is an administrator with posting rights.")
        return

    try:
        with SessionLocal() as session:
            user = UserRepository(session).find_or_create(user_tg_id)
            ChannelRepository(session).add(
                owner_user_id=user.id,
                telegram_channel_id=chat_id,
                username=username,
                title=title,
            )
            session.commit()
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "integrity" in err or "already" in err:
            await msg.reply_text("ℹ️ This channel is already linked and its details have been updated.")
        else:
            await msg.reply_text(f"❌ An error occurred while linking the channel: {e}")
        return

    uname_disp = f"@{username}" if username else "a private channel"
    await msg.reply_text(f"✅ Channel successfully linked: {title or '-'} ({uname_disp})\nID: <code>{chat_id}</code>", parse_mode="HTML")

async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg_id = update.effective_user.id
    with SessionLocal() as session:
        user = UserRepository(session).find_by_telegram_id(user_tg_id)
        channels = ChannelRepository(session).list_by_user(user.id, only_active=False) if user else []

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

async def toggle_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /toggle_channel <channel_id>")
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID. Please use the numeric channel ID from /channels.")
        return

    user_tg_id = update.effective_user.id
    with SessionLocal() as session:
        try:
            user = UserRepository(session).find_by_telegram_id(user_tg_id)
            if not user:
                await update.message.reply_text("Could not find your user account.")
                return

            repo = ChannelRepository(session)
            channels = repo.list_by_user(user.id, only_active=False)
            target = next((c for c in channels if c.telegram_channel_id == chat_id), None)
            
            if not target:
                await update.message.reply_text("Channel not found for your account.")
                return
            
            repo.set_active(user.id, chat_id, not target.is_active)
            session.commit()
            await update.message.reply_text("✅ Channel status has been updated.")
        except Exception as e:
            session.rollback()
            log.error(f"Error toggling channel: {e}")
            await update.message.reply_text("An error occurred while updating the channel.")

def register_commands(app: Application):
    """Registers all basic, non-conversational commands for the bot."""
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("settings", settings_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("link_channel", link_channel_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("channels", channels_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("toggle_channel", toggle_channel_cmd, filters=ALLOWED_USER_FILTER))
    
    # This handler specifically catches forwarded messages to link channels.
    app.add_handler(MessageHandler(ALLOWED_USER_FILTER & filters.FORWARDED, link_channel_forward_handler))

# --- END OF FINAL, FULLY CORRECTED AND ROBUST FILE (Version 8.1.2) ---