# --- START OF NEW, COMPLETE, AND MONETIZATION-READY FILE (Version 13.0.0) ---
# src/capitalguard/interfaces/telegram/admin_commands.py

import logging
import os

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, filters

from .helpers import get_service, unit_of_work
from capitalguard.infrastructure.db.repository import UserRepository

log = logging.getLogger(__name__)

# A simple but effective way to define admins via an environment variable for security.
# The filter will check if the command is coming from one of these usernames.
ADMIN_USERNAMES = [username.strip() for username in (os.getenv("ADMIN_USERNAMES") or "").split(',') if username]
admin_filter = filters.User(username=ADMIN_USERNAMES)

# --- Admin Command Handlers ---

@unit_of_work
async def grant_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """
    Admin Command: Grants a user access to the bot.
    Usage: /grantaccess <user_id>
    """
    if not context.args:
        await update.message.reply_text("Usage: /grantaccess <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)
        
        if not target_user:
            await update.message.reply_text(f"User with ID {target_user_id} not found in the database.")
            return
            
        if target_user.is_active:
            await update.message.reply_text(f"User {target_user_id} already has active access.")
            return

        target_user.is_active = True
        await update.message.reply_text(f"✅ Access granted to user {target_user_id}.")
        log.info(f"Admin {update.effective_user.username} granted access to user {target_user_id}.")

    except ValueError:
        await update.message.reply_text("Invalid User ID format. Please provide a numeric ID.")
    except Exception as e:
        log.error(f"Error in grant_access_cmd: {e}", exc_info=True)
        await update.message.reply_text("An unexpected error occurred while granting access.")

@unit_of_work
async def revoke_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """
    Admin Command: Revokes a user's access to the bot.
    Usage: /revokeaccess <user_id>
    """
    if not context.args:
        await update.message.reply_text("Usage: /revokeaccess <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)

        if not target_user:
            await update.message.reply_text(f"User with ID {target_user_id} not found in the database.")
            return

        if not target_user.is_active:
            await update.message.reply_text(f"User {target_user_id} is already inactive.")
            return

        target_user.is_active = False
        await update.message.reply_text(f"❌ Access revoked for user {target_user_id}.")
        log.info(f"Admin {update.effective_user.username} revoked access for user {target_user_id}.")

    except ValueError:
        await update.message.reply_text("Invalid User ID format. Please provide a numeric ID.")
    except Exception as e:
        log.error(f"Error in revoke_access_cmd: {e}", exc_info=True)
        await update.message.reply_text("An unexpected error occurred while revoking access.")

def register_admin_commands(app: Application):
    """Registers all admin-only commands for the bot."""
    if not ADMIN_USERNAMES:
        log.warning("ADMIN_USERNAMES environment variable is not set. Admin commands will not be available.")
        return
    
    app.add_handler(CommandHandler("grantaccess", grant_access_cmd, filters=admin_filter))
    app.add_handler(CommandHandler("revokeaccess", revoke_access_cmd, filters=admin_filter))
    log.info(f"Admin commands registered for users: {ADMIN_USERNAMES}")

# --- END OF NEW, COMPLETE, AND MONETIZATION-READY FILE ---