#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/admin_commands.py ---
# src/capitalguard/interfaces/telegram/admin_commands.py (v25.9 - Fix Keyword Arguments)
"""
Implements and registers all admin-only commands for the bot.
✅ THE FIX: Updated function signatures to accept 'db_user' and '**kwargs'.
   - The @uow_transaction decorator injects 'db_user', so handlers must accept it.
   - Added '**kwargs' to handle any future injected arguments gracefully.
✅ ADDED: Database Backup & Restore handlers.
"""

import logging
import os
import asyncio

from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import UserType
from capitalguard.infrastructure.db.backup_service import BackupService, auto_backup_loop
from capitalguard.config import settings

log = logging.getLogger(__name__)

ADMIN_USERNAMES = [username.strip() for username in (os.getenv("ADMIN_USERNAMES") or "").split(',') if username]
admin_filter = filters.User(username=ADMIN_USERNAMES)

@uow_transaction
async def grant_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user=None, **kwargs):
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
async def make_analyst_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user=None, **kwargs):
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

        target_user.user_type = UserType.ANALYST.value
        
        await update.message.reply_text(f"✅ User {target_user_id} has been promoted to Analyst.")
        log.info(f"Admin {update.effective_user.username} promoted user {target_user_id} to Analyst.")

    except (ValueError, IndexError):
        await update.message.reply_text("Invalid User ID format.")
    except Exception as e:
        log.error(f"Error in make_analyst_cmd: {e}", exc_info=True)
        await update.message.reply_text("An error occurred.")

@uow_transaction
async def revoke_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user=None, **kwargs):
    """Admin Command: Revokes a user's access to the bot."""
    if not context.args:
        await update.message.reply_text("Usage: /revokeaccess <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)

        if not target_user:
            await update.message.reply_text(f"User with ID {target_user_id} not found.")
            return

        if not target_user.is_active:
            await update.message.reply_text(f"User {target_user_id} is already inactive.")
            return

        target_user.is_active = False
        await update.message.reply_text(f"❌ Access revoked for user {target_user_id}.")
        log.info(f"Admin {update.effective_user.username} revoked access for user {target_user_id}.")

    except (ValueError, IndexError):
        await update.message.reply_text("Invalid User ID format.")
    except Exception as e:
        log.error(f"Error in revoke_access_cmd: {e}", exc_info=True)
        await update.message.reply_text("An error occurred.")

async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin Command: Requests a manual database backup."""
    # ✅ FIX: Match variable from .env
    admin_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    if str(update.effective_chat.id) != str(admin_id):
        return

    await update.message.reply_text("⏳ جاري تفريغ قاعدة البيانات وإنشاء النسخة الاحتياطية...")
    try:
        backup_path = await BackupService.create_backup()
        with open(backup_path, 'rb') as doc:
            await update.message.reply_document(
                document=doc, 
                caption="✅ تم إنشاء النسخة الاحتياطية (PostgreSQL) بنجاح."
            )
    except Exception as e:
        log.error(f"Backup command failed: {e}")
        await update.message.reply_text(f"❌ فشل إنشاء النسخة الاحتياطية: {str(e)}")

async def handle_restore_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin Listener: Handles SQL document uploads to restore the database."""
    # ✅ FIX: Match variable from .env
    admin_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    if str(update.effective_chat.id) != str(admin_id):
        return

    doc = update.message.document
    if doc and doc.file_name and doc.file_name.endswith(".sql"):
        await update.message.reply_text("⚠️ تم استلام ملف قاعدة البيانات (.sql). جاري الاسترجاع، سيتم استبدال البيانات الحالية...")
        try:
            file = await context.bot.get_file(doc.file_id)
            os.makedirs("backups", exist_ok=True)
            download_path = f"backups/restore_{doc.file_name}"
            await file.download_to_drive(download_path)
            
            # تنفيذ الاسترجاع (غير متزامن)
            await BackupService.restore_backup(download_path)
            
            await update.message.reply_text("✅ تمت عملية الاسترجاع بنجاح. النظام يعمل الآن على البيانات الجديدة.")
        except Exception as e:
            log.error(f"Restore operation failed: {e}")
            await update.message.reply_text(f"❌ حدث خطأ فادح أثناء الاسترجاع: {str(e)}")

def register_admin_commands(app: Application):
    """Registers all admin-only command handlers."""
    if not ADMIN_USERNAMES:
        log.warning("ADMIN_USERNAMES not set. Admin commands will be unavailable.")
        return
    
    app.add_handler(CommandHandler("grantaccess", grant_access_cmd, filters=admin_filter))
    app.add_handler(CommandHandler("makeanalyst", make_analyst_cmd, filters=admin_filter))
    app.add_handler(CommandHandler("revokeaccess", revoke_access_cmd, filters=admin_filter))
    
    # أوامر النسخ الاحتياطي
    app.add_handler(CommandHandler("backup", cmd_backup, filters=admin_filter))
    app.add_handler(MessageHandler(filters.Document.ALL & admin_filter, handle_restore_document))
    
    log.info(f"Admin commands registered for users: {ADMIN_USERNAMES}")
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/admin_commands.py ---