# src/capitalguard/interfaces/telegram/admin_commands.py (v25.5 - FINAL & CORRECTED)
"""
Implements and registers all admin-only commands for the bot.
"""

import logging
import os

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, filters

# ✅ **THE FIX:** Import the decorator from its definitive source.
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import UserType

log = logging.getLogger(__name__)

ADMIN_USERNAMES = [username.strip() for username in (os.getenv("ADMIN_USERNAMES") or "").split(',') if username]
admin_filter = filters.User(username=ADMIN_USERNAMES)

@uow_transaction
async def promote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """
    Admin Command: Promotes a user to the Analyst role, giving them the ability
    to create recommendations.
    """
    if not context.args:
        await update.message.reply_text("Usage: /promote <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)
        
        if not target_user:
            await update.message.reply_text(f"User with ID {target_user_id} not found. They must /start the bot first.")
            return
        
        if target_user.user_type == UserType.ANALYST:
            await update.message.reply_text(f"User {target_user_id} is already an Analyst.")
            return

        target_user.user_type = UserType.ANALYST
        # ✅ THE FIX: Provide clear, actionable feedback to the admin and the user.
        await update.message.reply_text(f"✅ User {target_user_id} has been promoted to Analyst.")
        log.info(f"Admin {update.effective_user.username} promoted user {target_user_id} to Analyst.")

        # Notify the user they have been promoted
        await context.bot.send_message(
            chat_id=target_user_id,
            text="🎉 Congratulations! You have been promoted to an **Analyst**. You can now use the `/newrec` command to create recommendations.",
            parse_mode="Markdown"
        )

    except (ValueError, IndexError):
        await update.message.reply_text("Invalid User ID format. Please provide a valid integer ID.")
    except Exception as e:
        log.error(f"Error in promote_cmd: {e}", exc_info=True)
        await update.message.reply_text("An unexpected error occurred.")

def register_admin_commands(app: Application):
    """Registers all admin-only command handlers."""
    if not ADMIN_USERNAMES:
        log.warning("ADMIN_USERNAMES not set. Admin commands will be unavailable.")
        return
    
    # ✅ THE FIX: Register the new /promote command and remove the obsolete ones.
    # This keeps the admin interface clean and aligned with the new user management workflow.
    app.add_handler(CommandHandler("promote", promote_cmd, filters=admin_filter))
    log.info(f"Admin commands registered for users: {ADMIN_USERNAMES}")

#END