# src/capitalguard/interfaces/telegram/admin_commands.py (v25.4 - FINAL & CORRECTED)
"""
Implements and registers all admin-only commands for the bot.
"""

import logging
import os

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, filters

# ✅ **THE FIX:** Import the decorator from its definitive source, not from helpers.
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import UserType

log = logging.getLogger(__name__)

ADMIN_USERNAMES = [username.strip() for username in (os.getenv("ADMIN_USERNAMES") or "").split(',') if username]
admin_filter = filters.User(username=ADMIN_USERNAMES)

@uow_transaction
async def grant_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """Admin Command: Grants a user access to the bot."""
    if not context.args:
        await update.message.reply_text("Usage: /grantaccess <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)
        
        if not target_user:
            await update.message.reply_text(f"User with ID {target_user_id} not found.")
            return
            
        if target_user.is_active:
            await update.message.reply_text(f"User {target_user_id} already has active access.")
            return

        target_user.is_active = True
        await update.message.reply_text(f"✅ Access granted to user {target_user_id}.")
        log.info(f"Admin {update.effective_user.username} granted access to user {target_user_id}.")

    except (ValueError, IndexError):
        await update.message.reply_text("Invalid User ID format.")
    except Exception as e:
        log.error(f"Error in grant_access_cmd: {e}", exc_info=True)
        await update.message.reply_text("An error occurred.")

@uow_transaction
async def make_analyst_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """Admin Command: Promotes a user to the Analyst role."""
    if not context.args:
        await update.message.reply_text("Usage: /makeanalyst <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)
        
        if not target_user:
            await update.message.reply_text(f"User with ID {target_user_id} not found.")
            return
        
        if target_user.user_type == UserType.ANALYST:
            await update.message.reply_text(f"User {target_user_id} is already an analyst.")
            return

        target_user.user_type = UserType.ANALYST
        await update.message.reply_text(f"✅ User {target_user_id} has been promoted to Analyst.")
        log.info(f"Admin {update.effective_user.username} promoted user {target_user_id} to Analyst.")

    except (ValueError, IndexError):
        await update.message.reply_text("Invalid User ID format.")
    except Exception as e:
        log.error(f"Error in make_analyst_cmd: {e}", exc_info=True)
        await update.message.reply_text("An error occurred.")

def register_admin_commands(app: Application):
    """Registers all admin-only command handlers."""
    if not ADMIN_USERNAMES:
        log.warning("ADMIN_USERNAMES not set. Admin commands will be unavailable.")
        return
    
    app.add_handler(CommandHandler("grantaccess", grant_access_cmd, filters=admin_filter))
    app.add_handler(CommandHandler("makeanalyst", make_analyst_cmd, filters=admin_filter))
    log.info(f"Admin commands registered for users: {ADMIN_USERNAMES}")

#END