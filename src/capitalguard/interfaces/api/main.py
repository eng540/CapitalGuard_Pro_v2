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
# from capitalguard.interfaces.api.routers import auth as auth_router  # اختياري

app = FastAPI(title="CapitalGuard Pro API", version="3.1.1")

_services_pack: dict | None = None  # نحتفظ بنسخة لنعيد الحقن بعد initialize

def create_ptb_app() -> Application:
    """
    إنشاء تطبيق تيليجرام + إنشاء الخدمات وحقنها في bot_data (مبدئيًا).
    سنعيد الحقن بعد initialize بسبب PicklePersistence.
    """
    global _services_pack

    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()

    # إنشاء الخدمات مرة واحدة
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    _services_pack = {
        "trade_service":     TradeService(repo, notifier),
        "report_service":    ReportService(repo),
        "analytics_service": AnalyticsService(repo),
    }

    # حقن مبدئي (قد يُطغى عليه عند initialize)
    application.bot_data.update(_services_pack)

    # تسجيل المعالجات (المعالجات تعتمد على قراءة المفاتيح من bot_data)
    register_all_handlers(application)
    return application

# --- Telegram Webhook Setup ---
ptb_app: Application | None = None
if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = create_ptb_app()

    @app.on_event("startup")
    async def on_startup():
        await ptb_app.initialize()

        # ✅ مهم: إعادة الحقن بعد أن يقوم PicklePersistence بتحميل bot_data
        if _services_pack:
            ptb_app.bot_data.update(_services_pack)
            logging.info("Re-injected services into bot_data after initialize().")

        if settings.TELEGRAM_WEBHOOK_URL:
            await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
            logging.info("Telegram webhook set to: %s", settings.TELEGRAM_WEBHOOK_URL)

    @app.on_event("shutdown")
    async def on_shutdown():
        try:
            if settings.TELEGRAM_WEBHOOK_URL:
                await ptb_app.bot.delete_webhook()
        except Exception:
            pass
        await ptb_app.shutdown()

    @app.post("/webhook/telegram")
    async def telegram_webhook(request: Request):
        if not ptb_app:
            return JSONResponse({"status": "disabled"}, status_code=503)
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

# اختياري: ضم راوترات أخرى
# app.include_router(auth_router.router)
#--- END OF FILE ---