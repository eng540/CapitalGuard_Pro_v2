#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/backup_service.py ---
import os
import asyncio
import datetime
import logging
import requests
from pathlib import Path
from capitalguard.config import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)

class BackupService:
    @staticmethod
    def _get_pg_url() -> str:
        """استخراج وتنظيف رابط قاعدة البيانات ليتوافق مع أدوات النظام"""
        url = str(getattr(settings, "DATABASE_URL", ""))
        # إزالة محولات SQLAlchemy لأن أدوات pg_dump/psql لا تتعرف عليها
        if "+psycopg" in url:
            url = url.replace("+psycopg", "")
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        return url

    @staticmethod
    async def create_backup() -> str:
        """ينشئ نسخة احتياطية غير متزامنة لتجنب تجميد البوت"""
        connection = BackupService._get_pg_url()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"backup_pg_{timestamp}.sql"
        
        # استخدام خيارات -c لتنظيف الجداول قبل الاسترجاع، وتجاهل الصلاحيات لتفادي مشاكل Supabase
        cmd = ["pg_dump", connection, "-f", str(backup_path), "-c", "--no-owner", "--no-privileges"]
        
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"pg_dump error: {stderr.decode()}")
            raise Exception(f"فشل إنشاء النسخة الاحتياطية (pg_dump). تحقق من السجلات.")
            
        return str(backup_path)

    @staticmethod
    async def restore_backup(backup_file_path: str):
        """يسترجع البيانات من ملف SQL معين بشكل غير متزامن"""
        connection = BackupService._get_pg_url()
        cmd = ["psql", connection, "-f", backup_file_path]
        
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"psql error: {stderr.decode()}")
            raise Exception(f"فشل استرجاع البيانات (psql). تحقق من السجلات.")

async def auto_backup_loop():
    """مهمة خلفية لإجراء النسخ الاحتياطي التلقائي كل 12 ساعة وإرساله عبر تليجرام"""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    # ✅ FIX: Updated to match .env.example variable name exactly
    admin_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None) 
    
    if not token or not admin_id:
        logger.warning("الرموز TELEGRAM_BOT_TOKEN أو TELEGRAM_ADMIN_CHAT_ID مفقودة. تم تعطيل النسخ التلقائي.")
        return

    while True:
        # الانتظار لمدة 12 ساعة (43200 ثانية)
        await asyncio.sleep(12 * 3600)  
        logger.info("بدء النسخ الاحتياطي المجدول لقاعدة بيانات PostgreSQL...")
        try:
            backup_path = await BackupService.create_backup()
            url = f"https://api.telegram.org/bot{token}/sendDocument"
            with open(backup_path, 'rb') as f:
                resp = requests.post(
                    url, 
                    data={'chat_id': admin_id, 'caption': '📦 النسخة الاحتياطية التلقائية (كل 12 ساعة)'}, 
                    files={'document': f}
                )
                if resp.status_code == 200:
                    logger.info("تم إرسال النسخة التلقائية للمدير بنجاح.")
                else:
                    logger.error(f"فشل إرسال النسخة عبر تليجرام: {resp.text}")
        except Exception as e:
            logger.error(f"خطأ في حلقة النسخ التلقائي: {str(e)}")
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/backup_service.py ---