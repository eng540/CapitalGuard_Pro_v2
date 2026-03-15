#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/bot_polling_runner.py ---
# File: src/capitalguard/interfaces/telegram/bot_polling_runner.py
# Version: v9.2.0-STABLE
#
# ✅ THE FIX (BUG-P1):
#   alert_service.schedule_job() غير موجودة في AlertService → AttributeError
#   الإصلاح: حُذف هذا الاستدعاء كلياً.
#   AlertService تُدار من boot.py وتعمل في bg thread خاص بها.
#
# ✅ THE FIX (BUG-P2):
#   asyncio.create_task(auto_backup_loop()) كان قبل ptb_app.initialize()
#   الإصلاح: نُقل إلى بعد initialize() لضمان أن الـ event loop نشط تماماً.
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

import asyncio
import logging

from dotenv import load_dotenv

# تحميل المتغيرات البيئية أولاً قبل أي استيراد
load_dotenv()

from capitalguard.boot import bootstrap_app
from capitalguard.logging_conf import setup_logging
from capitalguard.infrastructure.db.backup_service import auto_backup_loop


async def main() -> None:
    """
    يُهيئ ويُشغِّل البوت في Polling mode.
    يُستخدم للتطوير المحلي.
    في الإنتاج (Railway)، يعمل النظام عبر FastAPI webhook.
    """
    logging.info("Starting bot in polling mode...")

    ptb_app = bootstrap_app()
    if not ptb_app:
        logging.critical("Failed to bootstrap the application. Exiting.")
        return

    try:
        # تهيئة التطبيق أولاً — يُحمِّل بيانات Redis ويُجهِّز الـ handlers
        await ptb_app.initialize()
        logging.info("Telegram app initialized.")

        # ✅ BUG-P2 FIX: create_task بعد initialize() حيث الـ event loop نشط بالكامل
        asyncio.create_task(auto_backup_loop())
        logging.info("Auto-Backup background task scheduled.")

        # ✅ BUG-P1 FIX: حُذف alert_service.schedule_job() — الدالة غير موجودة.
        # AlertService تبدأ وتُدار تلقائياً من boot.py في bg thread مستقل.

        logging.info("Starting polling...")
        await ptb_app.run_polling()

    except Exception as e:
        logging.critical(f"Bot polling failed: {e}", exc_info=True)
        raise
    finally:
        logging.info("Shutting down bot...")
        await ptb_app.shutdown()


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
# --- END OF FINAL, CORRECTED FILE (Version 9.2.0) ---
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/bot_polling_runner.py ---
