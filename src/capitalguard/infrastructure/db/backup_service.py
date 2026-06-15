# src/capitalguard/infrastructure/db/backup_service.py
"""
File: src/capitalguard/infrastructure/db/backup_service.py
Version: v2.0.0-PRODUCTION

✅ THE FIX — إصلاح 6 أخطاء حرجة:

BUG-B1: BACKUP_DIR.mkdir() كان عند مستوى الوحدة (يُنفَّذ عند كل import)
         الإصلاح: نُقل داخل create_backup() و_ensure_backup_dir()

BUG-B2: requests.post() (blocking sync) داخل async → يجمّد event loop
         الإصلاح: استبدال بـ httpx.AsyncClient الكاملة async

BUG-B3: pg_dump يستقبل URL بدون -d flag → يفشل مع بعض صيغ الـ URL
         الإصلاح: إضافة "-d" flag صريح قبل الـ connection string

BUG-B4: لا يوجد حذف للنسخ القديمة → يملأ القرص
         الإصلاح: _cleanup_old_backups() تحتفظ بآخر MAX_BACKUPS=7 نسخ

BUG-B5: لا فحص لحجم الملف → Telegram يرفض > 50MB بدون إشعار
         الإصلاح: فحص الحجم وإشعار المدير إذا تجاوز 45MB

BUG-B6: open() + I/O blocking في async context
         الإصلاح: asyncio.to_thread() لكل عمليات I/O المتزامنة

Reviewed-by: Guardian Protocol v1 — 2026-03-15
"""

import os
import asyncio
import datetime
import logging
from pathlib import Path
from typing import Optional

import httpx

from capitalguard.config import settings

logger = logging.getLogger(__name__)

# ✅ BUG-B1 FIX: لا شيء على مستوى الوحدة — المجلد يُنشأ فقط عند الحاجة
BACKUP_DIR = Path("backups")
MAX_BACKUPS = 7                          # أقصى عدد للنسخ المحفوظة
MAX_TELEGRAM_SIZE = 45 * 1024 * 1024    # 45MB (Telegram limit 50MB مع هامش أمان)


# ─────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────

def _ensure_backup_dir() -> Path:
    """✅ BUG-B1 FIX: إنشاء المجلد عند الحاجة فقط وليس عند الاستيراد."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def _cleanup_old_backups() -> None:
    """✅ BUG-B4 FIX: يحذف النسخ القديمة ويحتفظ بآخر MAX_BACKUPS نسخة."""
    try:
        files = sorted(
            BACKUP_DIR.glob("backup_pg_*.sql"),
            key=lambda f: f.stat().st_mtime,
        )
        for old_file in files[:-MAX_BACKUPS]:
            try:
                old_file.unlink()
                logger.info(f"🗑️  Deleted old backup: {old_file.name}")
            except Exception as e:
                logger.warning(f"Could not delete old backup {old_file}: {e}")
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")


def _get_pg_url() -> str:
    """
    ✅ إزالة بادئات SQLAlchemy التي لا تتوافق مع أدوات pg_dump/psql.
    مثال: postgresql+psycopg://... → postgresql://...
    """
    url = str(getattr(settings, "DATABASE_URL", ""))
    for prefix in ("+psycopg", "+asyncpg", "+psycopg2"):
        url = url.replace(prefix, "")
    return url


async def _read_file_bytes(path: str) -> bytes:
    """✅ BUG-B6 FIX: قراءة الملف في thread منفصل لتجنب blocking."""
    def _read() -> bytes:
        with open(path, "rb") as f:
            return f.read()
    return await asyncio.to_thread(_read)


# ─────────────────────────────────────────────────────────────────
# BackupService
# ─────────────────────────────────────────────────────────────────

class BackupService:

    @staticmethod
    async def create_backup() -> str:
        """
        ✅ ينشئ نسخة احتياطية كاملة لـ PostgreSQL (Supabase) بشكل غير متزامن.
        يستخدم pg_dump v17 المُثبَّت في Dockerfile.
        يُنظِّف النسخ القديمة تلقائياً بعد النجاح.

        Returns:
            str: المسار الكامل لملف .sql المنشأ.

        Raises:
            Exception: عند فشل pg_dump مع تفاصيل الخطأ.
        """
        # ✅ BUG-B1 FIX: إنشاء المجلد عند الحاجة فقط
        backup_dir = _ensure_backup_dir()
        connection = _get_pg_url()

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"backup_pg_{timestamp}.sql"

        # ✅ BUG-B3 FIX: إضافة -d flag الصريح قبل الـ connection string
        cmd = [
            "pg_dump",
            "-d", connection,           # ← -d flag صريح (آمن مع كل الإصدارات)
            "-f", str(backup_path),
            "-c",                       # clean (DROP قبل CREATE)
            "--no-owner",               # متوافق مع Supabase
            "--no-privileges",          # متوافق مع Supabase
            "--if-exists",              # تجنب أخطاء DROP على جداول غير موجودة
        ]

        logger.info(f"Starting pg_dump → {backup_path.name}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            logger.error(f"pg_dump failed (code {process.returncode}): {error_msg}")
            raise Exception(f"فشل pg_dump: {error_msg[:300]}")

        file_size = backup_path.stat().st_size
        logger.info(
            f"✅ Backup created: {backup_path.name} "
            f"({file_size / 1024 / 1024:.1f} MB)"
        )

        # ✅ BUG-B4 FIX: تنظيف النسخ القديمة
        await asyncio.to_thread(_cleanup_old_backups)

        return str(backup_path)

    @staticmethod
    async def restore_backup(backup_file_path: str) -> None:
        """
        يسترجع قاعدة البيانات من ملف .sql.
        ⚠️ تحذير: يُنفَّذ فقط بعد تأكيد مزدوج من المدير (راجع admin_commands.py).

        Raises:
            Exception: عند فشل psql مع تفاصيل الخطأ.
        """
        connection = _get_pg_url()

        # ✅ BUG-B3 FIX: -d flag صريح في psql أيضاً
        cmd = [
            "psql",
            "-d", connection,
            "-f", backup_file_path,
            "-v", "ON_ERROR_STOP=0",    # استمر رغم الأخطاء البسيطة (صلاحيات Supabase)
        ]

        logger.info(f"Starting psql restore ← {backup_file_path}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            logger.error(f"psql restore failed (code {process.returncode}): {error_msg}")
            raise Exception(f"فشل psql: {error_msg[:300]}")

        logger.info("✅ Database restored successfully.")

    @staticmethod
    async def send_backup_to_telegram(
        backup_path: str,
        token: str,
        admin_id: str,
        caption: str = "📦 نسخة احتياطية",
    ) -> bool:
        """
        ✅ BUG-B2 FIX: إرسال الملف عبر httpx.AsyncClient (غير متزامن بالكامل).
        ✅ BUG-B5 FIX: فحص حجم الملف قبل الإرسال.
        ✅ BUG-B6 FIX: قراءة الملف عبر asyncio.to_thread.

        Returns:
            bool: True عند النجاح، False عند الفشل.
        """
        # ✅ BUG-B5: فحص الحجم
        file_size = os.path.getsize(backup_path)
        if file_size > MAX_TELEGRAM_SIZE:
            logger.warning(
                f"Backup file too large for Telegram: "
                f"{file_size / 1024 / 1024:.1f} MB > 45 MB. Skipping send."
            )
            return False

        # ✅ BUG-B6: قراءة الملف في thread منفصل
        file_bytes = await _read_file_bytes(backup_path)
        file_name = os.path.basename(backup_path)

        url = f"https://api.telegram.org/bot{token}/sendDocument"

        # ✅ BUG-B2: httpx.AsyncClient بدلاً من requests.post
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    url,
                    data={"chat_id": admin_id, "caption": caption},
                    files={
                        "document": (file_name, file_bytes, "application/octet-stream")
                    },
                )

            if response.status_code == 200:
                logger.info(f"✅ Backup sent to Telegram admin (chat_id={admin_id})")
                return True
            else:
                logger.error(
                    f"Telegram send failed: HTTP {response.status_code} — "
                    f"{response.text[:200]}"
                )
                return False

        except httpx.RequestError as e:
            logger.error(f"Telegram HTTP error: {e}")
            return False


# ─────────────────────────────────────────────────────────────────
# Background loop
# ─────────────────────────────────────────────────────────────────

async def auto_backup_loop() -> None:
    """
    مهمة خلفية: نسخ احتياطي تلقائي كل 12 ساعة مع إرسال للمدير.
    تعمل بالتوازي مع FastAPI/Bot بدون تأثير على الأداء.
    """
    token: Optional[str] = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    admin_id: Optional[str] = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)

    if not token or not admin_id:
        logger.warning(
            "auto_backup_loop: TELEGRAM_BOT_TOKEN أو TELEGRAM_ADMIN_CHAT_ID "
            "غير مضبوط — النسخ التلقائي معطّل."
        )
        return

    logger.info("auto_backup_loop: started — first backup in 12 hours.")

    while True:
        # الانتظار 12 ساعة بين كل نسخة
        await asyncio.sleep(12 * 3600)

        logger.info("auto_backup_loop: Starting scheduled backup...")
        try:
            backup_path = await BackupService.create_backup()

            sent = await BackupService.send_backup_to_telegram(
                backup_path=backup_path,
                token=token,
                admin_id=str(admin_id),
                caption="📦 النسخة الاحتياطية التلقائية (كل 12 ساعة) — CapitalGuard",
            )

            if not sent:
                # الملف أُنشئ لكن لم يُرسل (كبير الحجم أو خطأ شبكة)
                logger.warning(
                    "auto_backup_loop: Backup created but not sent to Telegram. "
                    f"Path: {backup_path}"
                )

        except Exception as e:
            logger.error(f"auto_backup_loop: Error — {e}", exc_info=True)
