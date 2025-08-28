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

app = FastAPI(title="CapitalGuard Pro API", version="3.0.0")

def create_ptb_app() -> Application:
    """
    إنشاء تطبيق تيليجرام مرة واحدة + إنشاء الخدمات وتمريرها صراحةً للمعالجات.
    """
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    application = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

    # إنشاء الخدمات مرة واحدة
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    services = {
        "trade": TradeService(repo, notifier),
        "report": ReportService(repo),
        "analytics": AnalyticsService(repo),
    }

    # تسجيل جميع المعالجات مع تمرير الخدمات صراحةً
    register_all_handlers(application, services)

    return application

# --- Telegram Webhook Setup ---
ptb_app: Application | None = None
if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = create_ptb_app()

    @app.on_event("startup")
    async def on_startup():
        await ptb_app.initialize()
        if settings.TELEGRAM_WEBHOOK_URL:
            await ptb_app.bot.set_webhook(
                url=settings.TELEGRAM_WEBHOOK_URL,
                allowed_updates=Update.ALL_TYPES,
            )
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

# اختيارياً: ضم راوترات أخرى إن وُجدت بدون كسر التشغيل
try:
    from capitalguard.interfaces.api.routers import auth as auth_router  # type: ignore
    app.include_router(auth_router.router)
except Exception:
    logging.info("Auth router not found or failed to load — continuing without it.")
#--- END OF FILE ---