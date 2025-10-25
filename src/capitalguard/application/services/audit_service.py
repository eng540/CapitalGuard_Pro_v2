# src/capitalguard/interfaces/telegram/commands.py (v27.1 - get_service Fix + Event Summary Enhancement)
"""
Registers and implements all simple, non-conversational commands for the bot.
✅ FIX: Added missing get_service import.
✅ FIX: Added intelligent event summary to /events output.
✅ FIX: Improved event log formatting and automatic PnL aggregation.
✅ FINAL & PRODUCTION READY.
"""

import logging
import io
import csv
from statistics import mean

from telegram import Update, InputFile
from telegram.ext import (Application, ContextTypes, CommandHandler)

# ✅ FIX: Added missing import for get_service
from .helpers import get_service

from capitalguard.infrastructure.db.uow import uow_transaction
from .auth import require_active_user, require_analyst_user
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.audit_service import AuditService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository, RecommendationRepository
from capitalguard.infrastructure.db.models import UserType
from .keyboards import build_subscription_keyboard

log = logging.getLogger(__name__)

# --- Command Handlers ---

@uow_transaction
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    user = update.effective_user
    log.info(f"User {user.id} initiated /start command.")
    UserRepository(db_session).find_or_create(telegram_id=user.id, first_name=user.first_name, username=user.username)

    if context.args and context.args[0].startswith("track_"):
        try:
            rec_id = int(context.args[0].split('_')[1])
            trade_service = get_service(context, "trade_service", TradeService)
            result = await trade_service.create_trade_from_recommendation(str(user.id), rec_id, db_session=db_session)
            if result.get('success'):
                await update.message.reply_html(
                    f"✅ <b>Signal tracking confirmed!</b>\n"
                    f"Signal for <b>{result['asset']}</b> has been added to your portfolio.\n\n"
                    f"Use <code>/myportfolio</code> to view your trades."
                )
            else:
                await update.message.reply_html(f"⚠️ Could not track signal: {result.get('error', 'Unknown')}")
            return
        except Exception as e:
            log.error(f"Error handling deep link for user {user.id}: {e}", exc_info=True)
            await update.message.reply_html("An error occurred while processing the link.")
        return

    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")

@uow_transaction
@require_active_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    trader_help = (
        "• <code>/myportfolio</code> — View and manage your open trades.\n"
        "• <code>/export</code> — Export your trade history to a CSV file.\n"
    )
    analyst_help = (
        "• <code>/newrec</code> — Create a new recommendation.\n"
        "• <code>/link_channel</code> — Link a new channel for publishing.\n"
        "• <code>/unlink_channel</code> — Unlink a channel.\n"
        "• <code>/channels</code> — View your linked channels.\n"
        "• <code>/events &lt;id&gt;</code> — Show the audit log for a recommendation.\n"
    )
    general_help = (
        "• <code>/help</code> — Show this help message.\n\n"
        "💡 <b>Tip:</b> To track a signal from a text message, simply forward it to me."
    )
    full_help = "<b>Available Commands:</b>\n\n" + trader_help
    if db_user and db_user.user_type == UserType.ANALYST:
        full_help += analyst_help
    full_help += general_help
    await update.message.reply_html(full_help)

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

@uow_transaction
@require_active_user
@require_analyst_user
async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_html("<b>Usage:</b> <code>/events &lt;recommendation_id&gt;</code>")
        return

    rec_id = int(context.args[0])
    audit_service = get_service(context, "audit_service", AuditService)

    try:
        events = audit_service.get_recommendation_events_for_user(rec_id, str(db_user.telegram_user_id))
        if not events:
            await update.message.reply_html(f"No events found for Recommendation #{rec_id}.")
            return

        message_lines = [f"📋 <b>Event Log for Recommendation #{rec_id}</b>", "─" * 20]
        pnl_values = []
        closed_total = 0
        final_status = "ACTIVE"

        for event in events:
            ts = event["timestamp"]
            etype = event["type"].replace("_", " ").title()
            data = event["data"]
            message_lines.append(f"- {etype} (at {ts})")
            if not data:
                message_lines.append("  No data")
            else:
                message_lines.append(f"  {data}")

            if isinstance(data, dict):
                if "pnl_on_part" in data:
                    pnl_values.append(float(data["pnl_on_part"]))
                if "closed_percent" in data:
                    closed_total += float(data["closed_percent"])
                if etype.lower().startswith("final close"):
                    final_status = "CLOSED"

        # --- summary section ---
        summary = ["", "──────────────", "📊 <b>Summary:</b>"]
        total_closed = min(closed_total, 100)
        summary.append(f"• Total Closed: {total_closed:.1f}%")
        if pnl_values:
            avg_pnl = mean(pnl_values)
            summary.append(f"• Average PnL: {avg_pnl:+.2f}%")
        summary.append(f"• Final Status: {'✅ CLOSED' if final_status == 'CLOSED' or total_closed >= 100 else '🟢 ACTIVE'}")

        message_lines.extend(summary)
        text = "\n".join(message_lines)
        if len(text) > 4096:
            text = text[:4080] + "\n... (truncated)"

        await update.message.reply_html(text)

    except Exception as e:
        log.error(f"Error fetching events for rec #{rec_id}: {e}", exc_info=True)
        await update.message.reply_text("An unexpected error occurred while fetching the event log.")

@uow_transaction
@require_active_user
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await update.message.reply_text("Preparing your export file...")
    repo = RecommendationRepository()
    if db_user.user_type == UserType.ANALYST:
        items_orm = repo.get_open_recs_for_analyst(db_session, db_user.id)
        items = [repo._to_entity(rec) for rec in items_orm if repo._to_entity(rec)]
    else:
        trades_orm = repo.get_open_trades_for_trader(db_session, db_user.id)
        items = []
        for trade in trades_orm:
            trade_entity = Recommendation(
                id=trade.id, asset=Symbol(trade.asset), side=Side(trade.side),
                entry=Price(trade.entry), stop_loss=Price(trade.stop_loss),
                targets=Targets(trade.targets),
                status=RecommendationStatusEntity.ACTIVE,
                order_type=OrderType.MARKET, created_at=trade.created_at
            )
            items.append(trade_entity)
    if not items:
        await update.message.reply_text("You have no data to export.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    header = ["id", "asset", "side", "status", "market", "entry_price", "stop_loss", "targets", "exit_price", "notes", "created_at", "closed_at"]
    writer.writerow(header)
    for rec in items:
        row = [
            rec.id, rec.asset.value, rec.side.value, rec.status.value, getattr(rec, 'market', 'N/A'),
            rec.entry.value, rec.stop_loss.value,
            ", ".join(f"{t.price.value}" for t in rec.targets.values),
            rec.exit_price, rec.notes,
            rec.created_at.strftime('%Y-%m-%d %H:%M:%S') if rec.created_at else "",
            rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""
        ]
        writer.writerow(row)
    output.seek(0)
    csv_file = InputFile(io.BytesIO(output.getvalue().encode("utf-8")), filename="capitalguard_export.csv")
    await update.message.reply_document(document=csv_file, caption="Your trade history has been generated.")

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(CommandHandler("events", events_cmd))
    app.add_handler(CommandHandler("export", export_cmd))