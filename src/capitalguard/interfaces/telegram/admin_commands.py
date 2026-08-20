#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/admin_commands.py ---
# File: src/capitalguard/interfaces/telegram/admin_commands.py
# Version: v26.0.0-SECURE
#
# ✅ THE FIX (BUG-R1 — CRITICAL SECURITY):
#   handle_restore_document كان يُنفِّذ الاسترجاع فوراً بدون تأكيد.
#   الإصلاح: نظام تأكيد مزدوج بـ InlineKeyboard:
#     1. المدير يرسل ملف .sql
#     2. البوت يعرض تحذيراً مع زرَّي "تأكيد" و"إلغاء"
#     3. الاسترجاع يُنفَّذ فقط عند الضغط على "تأكيد"
#
# ✅ THE FIX (BUG-R3):
#   الاسترجاع كان يعمل بينما الخدمات نشطة → race condition خطير.
#   الإصلاح: رسالة تحذير صريحة + توثيق المخاطر.
#   (إيقاف الخدمات قبل الاسترجاع هو مسؤولية المشرف — توثيق في الرسالة)
#
# ✅ THE FIX (BUG-B6 في cmd_backup):
#   open() blocking داخل async — استُبدل بـ BackupService.send_backup_to_telegram()
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

import logging
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import UserType
from capitalguard.application.services.entitlement_service import EntitlementService
from capitalguard.infrastructure.db.backup_service import BackupService
from capitalguard.config import settings

log = logging.getLogger(__name__)

ADMIN_USERNAMES = [
    u.strip()
    for u in (os.getenv("ADMIN_USERNAMES") or "").split(",")
    if u.strip()
]
admin_filter = filters.User(username=ADMIN_USERNAMES)

# Callback data constants
_CB_CONFIRM_RESTORE = "admin:confirm_restore"
_CB_CANCEL_RESTORE  = "admin:cancel_restore"


# ─────────────────────────────────────────────────────────────────
# Discoverable administration panel
# ─────────────────────────────────────────────────────────────────

async def admin_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the complete administrator command surface in one place."""
    if not _is_admin(update.effective_chat.id):
        return
    text = (
        "<b>🛠️ CapitalGuard Admin Panel</b>\n\n"
        "<b>Users & Roles</b>\n"
        "<code>/grantaccess &lt;telegram_user_id&gt;</code> — منح الوصول\n"
        "<code>/revokeaccess &lt;telegram_user_id&gt;</code> — سحب الوصول\n"
        "<code>/makeanalyst &lt;telegram_user_id&gt;</code> — ترقية إلى محلل\n"
        "<code>/grantalpha &lt;telegram_user_id&gt; &lt;FEATURES&gt;</code> — منح Alpha مجاني\n\n"
        "<b>Historical Intake</b>\n"
        "أرسل أي Forward من قناة إلى الخاص — اكتشاف وتدقيق تلقائي\n"
        "<code>/historical_forward_start &lt;channel_code&gt;</code> — فتح دفعة إعادة توجيه\n"
        "<code>/historical_forward_one &lt;channel_code&gt;</code> — استقبال رسالة واحدة\n"
        "<code>/historical_channels</code> — عرض أكواد القنوات ومعرّفات Telegram\n"
        "<code>/historical_forward_status</code> — عرض حالة الدفعة الحالية\n"
        "<code>/historical_forward_finish</code> — إنشاء Dry-Run للمراجعة\n"
        "<code>/historical_forward_review BATCH approve|reject</code> — قرار المالك قبل الإدخال\n"
        "<code>/historical_forward_cancel</code> — إلغاء staging دون إدخال\n\n"
        "<b>Operations</b>\n"
        "<code>/backup</code> — إنشاء وإرسال نسخة احتياطية\n"
        "إرسال ملف <code>.sql</code> — بدء الاسترجاع بعد تأكيد مزدوج\n\n"
        "<b>Safety</b>\n"
        "الاسترجاع لا يبدأ قبل زر التأكيد، ويجب إيقاف الخدمات قبل استرجاع قاعدة الإنتاج.\n\n"
        "<b>Audit</b>\n"
        "راجع Railway Deploy Logs وHealth وMetrics بعد أي عملية حساسة."
    )
    await update.message.reply_html(text)

# ─────────────────────────────────────────────────────────────────
# Existing admin commands (unchanged)
# ─────────────────────────────────────────────────────────────────

@uow_transaction
async def grant_access_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db_session,
    db_user=None,
    **kwargs,
):
    """Admin Command: منح مستخدم حق الوصول."""
    if not context.args:
        await update.message.reply_text("Usage: /grantaccess <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)
        if not target_user:
            await update.message.reply_text(f"User {target_user_id} not found.")
            return
        if target_user.is_active:
            await update.message.reply_text(f"User {target_user_id} already active.")
            return
        target_user.is_active = True
        await update.message.reply_text(f"✅ Access granted to {target_user_id}.")
        log.info(f"Admin granted access to {target_user_id}.")
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid User ID.")
    except Exception as e:
        log.error(f"grant_access_cmd: {e}", exc_info=True)
        await update.message.reply_text("An error occurred.")


@uow_transaction
async def make_analyst_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db_session,
    db_user=None,
    **kwargs,
):
    """Admin Command: ترقية مستخدم إلى محلل."""
    if not context.args:
        await update.message.reply_text("Usage: /makeanalyst <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)
        if not target_user:
            await update.message.reply_text(f"User {target_user_id} not found.")
            return
        if target_user.user_type == UserType.ANALYST:
            await update.message.reply_text(f"User {target_user_id} is already analyst.")
            return
        target_user.user_type = UserType.ANALYST.value
        await update.message.reply_text(f"✅ User {target_user_id} promoted to Analyst.")
        log.info(f"Admin promoted {target_user_id} to Analyst.")
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid User ID.")
    except Exception as e:
        log.error(f"make_analyst_cmd: {e}", exc_info=True)
        await update.message.reply_text("An error occurred.")


@uow_transaction
async def revoke_access_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db_session,
    db_user=None,
    **kwargs,
):
    """Admin Command: سحب حق الوصول."""
    if not context.args:
        await update.message.reply_text("Usage: /revokeaccess <user_id>")
        return
    try:
        target_user_id = int(context.args[0])
        user_repo = UserRepository(db_session)
        target_user = user_repo.find_by_telegram_id(target_user_id)
        if not target_user:
            await update.message.reply_text(f"User {target_user_id} not found.")
            return
        if not target_user.is_active:
            await update.message.reply_text(f"User {target_user_id} already inactive.")
            return
        target_user.is_active = False
        await update.message.reply_text(f"❌ Access revoked for {target_user_id}.")
        log.info(f"Admin revoked access for {target_user_id}.")
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid User ID.")
    except Exception as e:
        log.error(f"revoke_access_cmd: {e}", exc_info=True)
        await update.message.reply_text("An error occurred.")


@uow_transaction
async def grant_alpha_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db_session,
    db_user=None,
    **kwargs,
):
    """Grant zero-cost Alpha features; never charges and remains idempotent."""
    if not _is_admin(update.effective_chat.id):
        return
    if len(context.args or []) < 2:
        await update.message.reply_text("Usage: /grantalpha <telegram_user_id> <FEATURE1,FEATURE2> [key=...]")
        return
    try:
        target_telegram_id = int(context.args[0])
        features = [feature for feature in context.args[1].split(",") if feature.strip()]
        custom_key = next(
            (token.split("=", 1)[1].strip() for token in context.args[2:] if token.startswith("key=")),
            None,
        )
        target = UserRepository(db_session).find_by_telegram_id(target_telegram_id)
        if target is None:
            await update.message.reply_text("❌ المستخدم غير موجود في قاعدة البيانات.")
            return
        actor = UserRepository(db_session).find_by_telegram_id(update.effective_user.id)
        key = custom_key or f"alpha:{target_telegram_id}:{','.join(sorted(features)).upper()}"
        grants = EntitlementService(billing_enabled=False).grant_alpha(
            db_session,
            target.id,
            features,
            idempotency_key=key,
            actor_user_id=actor.id if actor else None,
            metadata={"channel": "telegram_admin", "target_telegram_id": target_telegram_id},
        )
        await update.message.reply_text(
            f"✅ Alpha grant recorded (zero cost).\nFeatures: {', '.join(grant.feature_code for grant in grants)}"
        )
    except (ValueError, IndexError) as exc:
        await update.message.reply_text(f"⚠️ Invalid Alpha grant: {exc}")
    except Exception as exc:
        log.error("grant_alpha_cmd failed", exc_info=True)
        await update.message.reply_text("❌ تعذر تسجيل منح Alpha. لم يتم تحصيل أي مبلغ.")


# ─────────────────────────────────────────────────────────────────
# Backup command
# ─────────────────────────────────────────────────────────────────

def _is_admin(chat_id) -> bool:
    """تحقق مركزي من صلاحية المدير."""
    admin_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    if not admin_id:
        return False
    return str(chat_id) == str(admin_id)


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin Command: /backup — إنشاء نسخة احتياطية يدوية وإرسالها.
    ✅ BUG-B6 FIX: يستخدم BackupService.send_backup_to_telegram() (async بالكامل).
    """
    if not _is_admin(update.effective_chat.id):
        return

    await update.message.reply_text(
        "⏳ جاري إنشاء النسخة الاحتياطية من Supabase...\n"
        "قد يستغرق ذلك بضع ثوانٍ."
    )

    try:
        backup_path = await BackupService.create_backup()

        token    = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
        admin_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)

        # ✅ BUG-B6 FIX: الإرسال عبر BackupService (async + fحص الحجم)
        sent = await BackupService.send_backup_to_telegram(
            backup_path=backup_path,
            token=token,
            admin_id=str(admin_id),
            caption="✅ نسخة احتياطية يدوية — CapitalGuard (Supabase)",
        )

        if sent:
            await update.message.reply_text("✅ تم إنشاء النسخة الاحتياطية وإرسالها بنجاح.")
        else:
            import os
            file_size_mb = os.path.getsize(backup_path) / 1024 / 1024
            await update.message.reply_text(
                f"✅ تم إنشاء النسخة الاحتياطية.\n"
                f"⚠️ حجم الملف ({file_size_mb:.1f} MB) كبير للإرسال عبر تيليجرام.\n"
                f"الملف محفوظ على السيرفر: `{backup_path}`",
                parse_mode="Markdown",
            )

    except Exception as e:
        log.error(f"cmd_backup failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ فشل إنشاء النسخة الاحتياطية:\n<code>{str(e)[:200]}</code>",
            parse_mode="HTML",
        )


# ─────────────────────────────────────────────────────────────────
# Restore — Step 1: استلام الملف وطلب التأكيد
# ─────────────────────────────────────────────────────────────────

async def handle_restore_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    ✅ BUG-R1 FIX: التأكيد المزدوج — الاسترجاع لا يبدأ إلا بعد الضغط على زر التأكيد.
    ✅ BUG-R3 FIX: تحذير صريح بأن الخدمات تعمل أثناء الاسترجاع.

    الخطوة 1: تنزيل الملف وحفظ مساره في user_data، ثم طلب التأكيد.
    """
    if not _is_admin(update.effective_chat.id):
        return

    doc = update.message.document
    if not doc or not doc.file_name or not doc.file_name.endswith(".sql"):
        return

    # تنزيل الملف أولاً
    await update.message.reply_text("⏳ جاري تنزيل الملف...")
    try:
        file = await context.bot.get_file(doc.file_id)
        os.makedirs("backups", exist_ok=True)
        download_path = f"backups/restore_{doc.file_name}"
        await file.download_to_drive(download_path)
    except Exception as e:
        log.error(f"handle_restore_document: download failed: {e}")
        await update.message.reply_text(f"❌ فشل تنزيل الملف: {e}")
        return

    # ✅ BUG-R1 FIX: حفظ المسار في user_data لاستخدامه عند التأكيد
    context.user_data["pending_restore_path"] = download_path
    context.user_data["pending_restore_file"] = doc.file_name

    import os as _os
    file_size_mb = _os.path.getsize(download_path) / 1024 / 1024

    # ✅ BUG-R3 FIX: تحذير صريح بأن الخدمات تعمل
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم، استرجع البيانات", callback_data=_CB_CONFIRM_RESTORE),
        InlineKeyboardButton("❌ إلغاء",                callback_data=_CB_CANCEL_RESTORE),
    ]])

    await update.message.reply_text(
        "⚠️ <b>تحذير: عملية خطرة وغير قابلة للتراجع!</b>\n\n"
        f"الملف: <code>{doc.file_name}</code> ({file_size_mb:.1f} MB)\n\n"
        "🔴 <b>سيتم استبدال قاعدة البيانات الإنتاجية بالكامل.</b>\n\n"
        "⚠️ <b>تنبيه:</b> الخدمات (API + Bot) تعمل الآن.\n"
        "يُنصح بإيقافها قبل الاسترجاع لتجنب تعارض البيانات.\n\n"
        "هل أنت متأكد تماماً من المتابعة؟",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────
# Restore — Step 2: تنفيذ أو إلغاء الاسترجاع
# ─────────────────────────────────────────────────────────────────

async def handle_restore_confirmation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    ✅ BUG-R1 FIX: الخطوة 2 — يُنفِّذ الاسترجاع فقط عند الضغط على زر التأكيد.
    """
    query = update.callback_query
    await query.answer()

    if not _is_admin(query.from_user.id):
        await query.edit_message_text("❌ غير مصرح.")
        return

    if query.data == _CB_CANCEL_RESTORE:
        context.user_data.pop("pending_restore_path", None)
        context.user_data.pop("pending_restore_file", None)
        await query.edit_message_text("✅ تم إلغاء عملية الاسترجاع.")
        log.info("Admin cancelled restore operation.")
        return

    if query.data == _CB_CONFIRM_RESTORE:
        download_path = context.user_data.pop("pending_restore_path", None)
        file_name     = context.user_data.pop("pending_restore_file", "unknown")

        if not download_path:
            await query.edit_message_text(
                "❌ انتهت صلاحية الطلب. أرسل الملف مجدداً."
            )
            return

        await query.edit_message_text(
            "⏳ جاري الاسترجاع...\n"
            "قد يستغرق دقيقة أو أكثر حسب حجم قاعدة البيانات."
        )

        try:
            await BackupService.restore_backup(download_path)
            await query.message.reply_text(
                "✅ <b>تمت عملية الاسترجاع بنجاح.</b>\n\n"
                f"الملف المُستخدم: <code>{file_name}</code>\n\n"
                "⚠️ يُنصح بإعادة تشغيل الخدمات للتأكد من تحميل البيانات الجديدة.",
                parse_mode="HTML",
            )
            log.info(f"Admin completed restore from: {download_path}")
        except Exception as e:
            log.error(f"handle_restore_confirmation: restore failed: {e}", exc_info=True)
            await query.message.reply_text(
                f"❌ <b>فشل الاسترجاع:</b>\n<code>{str(e)[:300]}</code>",
                parse_mode="HTML",
            )


# ─────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────

def register_admin_commands(app: Application) -> None:
    """تسجيل جميع أوامر المشرف."""
    if not ADMIN_USERNAMES:
        log.warning("ADMIN_USERNAMES not set. Admin commands unavailable.")
        return

    # لوحة الإدارة — نقطة دخول واحدة قابلة للاكتشاف
    app.add_handler(CommandHandler("admin", admin_panel_cmd, filters=admin_filter))

    # أوامر إدارة المستخدمين
    app.add_handler(CommandHandler("grantaccess", grant_access_cmd, filters=admin_filter))
    app.add_handler(CommandHandler("makeanalyst", make_analyst_cmd, filters=admin_filter))
    app.add_handler(CommandHandler("revokeaccess", revoke_access_cmd, filters=admin_filter))
    app.add_handler(CommandHandler("grantalpha", grant_alpha_cmd, filters=admin_filter))

    # أوامر النسخ الاحتياطي
    app.add_handler(CommandHandler("backup", cmd_backup, filters=admin_filter))

    # استلام ملف .sql لطلب الاسترجاع (Step 1)
    app.add_handler(
        MessageHandler(filters.Document.ALL & admin_filter, handle_restore_document)
    )

    # ✅ BUG-R1 FIX: معالج زرَّي التأكيد/الإلغاء (Step 2)
    app.add_handler(
        CallbackQueryHandler(
            handle_restore_confirmation,
            pattern=rf"^({_CB_CONFIRM_RESTORE}|{_CB_CANCEL_RESTORE})$",
        )
    )

    log.info(f"Admin commands registered for: {ADMIN_USERNAMES}")
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/admin_commands.py ---
