# src/capitalguard/interfaces/telegram/commands.py (v3.0 - Final Multi-Tenant)
import io
import csv
import logging
from typing import Optional, Tuple

from telegram import Update, InputFile
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

from .helpers import get_service, unit_of_work
from .auth import require_active_user, require_analyst_user
from .ui_texts import build_analyst_stats_text, build_trade_card_text
from .keyboards import build_open_recs_keyboard
from capitalguard.config import settings

from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.audit_service import AuditService
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

log = logging.getLogger(__name__)

AWAITING_FORWARD_KEY = "awaiting_forward_channel_link"

@unit_of_work
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    user = update.effective_user
    log.info(f"User {user.id} ({user.username}) started interaction.")
    UserRepository(db_session).find_or_create(telegram_id=user.id, first_name=user.first_name, username=user.username)
    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")

@require_active_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "<b>--- Trading ---</b>\n"
        "• <code>/open</code> — View your open positions.\n"
        "• <code>/log</code> — Manually log a new trade (Coming Soon).\n\n"
        "<b>--- Analyst Features ---</b>\n"
        "• <code>/newrec</code> — Create a new recommendation.\n\n"
        "<b>--- Analytics & Auditing ---</b>\n"
        "• <code>/stats</code> — View your performance summary.\n"
        "• <code>/events &lt;id&gt;</code> — Show event log for a position.\n\n"
        "<b>--- General ---</b>\n"
        "• <code>/cancel</code> — Cancel current operation."
    )

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
async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_html("<b>Usage:</b> <code>/events &lt;id&gt;</code>")
        return

    rec_id = int(context.args[0])
    user_telegram_id = str(update.effective_user.id)
    
    audit_service = get_service(context, "audit_service", AuditService)

    try:
        events = audit_service.get_recommendation_events_for_user(rec_id, user_telegram_id)
        
        if not events:
            await update.message.reply_html(f"No events found for Position #{rec_id}.")
            return

        message_lines = [f"📋 <b>Event Log for Position #{rec_id}</b>", "─" * 20]
        for event in events:
            timestamp = event['timestamp']
            event_type = event['type'].replace('_', ' ').title()
            data_str = str(event['data']) if event['data'] else "No data"
            message_lines.append(f"<b>- {event_type}</b> (at {timestamp})")
            message_lines.append(f"  <code>{data_str}</code>")

        message_text = "\n".join(message_lines)
        if len(message_text) > 4096:
            message_text = message_text[:4090] + "\n..."

        await update.message.reply_html(message_text)

    except ValueError as e:
        await update.message.reply_text(str(e))
    except Exception as e:
        log.error(f"Error fetching events for rec #{rec_id}: {e}", exc_info=True)
        await update.message.reply_text("An unexpected error occurred while fetching the event log.")

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("open", open_cmd))
    app.add_handler(CommandHandler("events", events_cmd))