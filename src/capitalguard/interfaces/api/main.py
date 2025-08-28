#--- START OF FILE: src/capitalguard/interfaces/api/main.py ---
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, PicklePersistence
from capitalguard.config import settings
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.interfaces.telegram.handlers import register_all_handlers

app = FastAPI(title="CapitalGuard Pro API", version="2.6.0")

def create_ptb_app() -> Application:
    """
    الدالة المركزية والوحيدة لإنشاء وإعداد تطبيق تليجرام.
    """
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()

    # إنشاء الخدمات مرة واحدة فقط عند بدء التشغيل
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    
    # حقن الخدمات في bot_data لتكون متاحة لكل المعالجات
    application.bot_data["trade_service"] = TradeService(repo, notifier)
    application.bot_data["report_service"] = ReportService(repo)
    application.bot_data["analytics_service"] = AnalyticsService(repo)

    # تسجيل جميع المعالجات (الأوامر، المحادثات، الأزرار)
    register_all_handlers(application)
    
    return application

ptb_app: Application | None = None
if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = create_ptb_app()

    @app.on_event("startup")
    async def on_startup():
        await ptb_app.initialize()
        if settings.TELEGRAM_WEBHOOK_URL:
            await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
            logging.info(f"Telegram webhook set to: {settings.TELEGRAM_WEBHOOK_URL}")

    @app.on_event("shutdown")
    async def on_shutdown():
        await ptb_app.shutdown()

    @app.post("/webhook/telegram")
    async def telegram_webhook(request: Request):
        if ptb_app:
            try:
                data = await request.json()
                update = Update.de_json(data, ptb_app.bot)
                await ptb_app.process_update(update)
            except Exception as e:
                logging.exception("Error processing Telegram update: %s", e)
        return {"status": "ok"}
else:
    logging.warning("TELEGRAM_BOT_TOKEN not set; Telegram features disabled.")

@app.get("/")
def root():
    return {"message": "🚀 CapitalGuard API is running"}
#--- END OF FILE ---