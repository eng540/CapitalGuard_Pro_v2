# src/capitalguard/interfaces/telegram/commands.py (v3.0 - Final Multi-Tenant)
import logging
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler

from .helpers import get_service, unit_of_work
from .auth import require_active_user, require_analyst_user
from .keyboards import build_open_recs_keyboard
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.audit_service import AuditService
from capitalguard.infrastructure.db.repository import UserRepository

log = logging.getLogger(__name__)

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
        "• <code>/myportfolio</code> — View your open trades.\n"
        "• Forward any signal to me to start tracking!\n\n"
        "<b>--- Analyst Features ---</b>\n"
        "• <code>/newrec</code> — Create a new recommendation.\n\n"
        "<b>--- General ---</b>\n"
        "• <code>/help</code> — Show this help message."
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
        
    # Note: build_open_recs_keyboard needs to be adapted for UserTrade objects
    # For now, this will primarily work for analysts.
    keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)
    await update.message.reply_html("<b>📊 Your Open Positions</b>\nSelect one to manage:", reply_markup=keyboard)

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    # Renamed /open to /myportfolio for clarity for traders
    app.add_handler(CommandHandler(["myportfolio", "open"], open_cmd))