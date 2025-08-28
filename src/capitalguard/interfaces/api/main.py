# --- START OF FILE: src/capitalguard/interfaces/api/main.py ---
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from telegram import Update
from telegram.ext import Application, PicklePersistence

from capitalguard.config import settings
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier

# دالة التسجيل الموحّدة
from capitalguard.interfaces.telegram.handlers import register_all_handlers

# باقي الواجهات
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.schemas import RecommendationIn, RecommendationOut, CloseIn
from capitalguard.interfaces.api.security.deps import get_current_user, require_roles

app = FastAPI(title="CapitalGuard Pro API", version="2.5.0")

# (يمكن إبقاء إعدادات Sentry/RateLimit/CORS لديك كما هي في مشروعك)

def create_ptb_app() -> Application:
    """
    إنشاء وإعداد تطبيق تيليجرام مع حقن الخدمات قبل التسجيل.
    """
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    application = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

    # حقن الخدمات في bot_data
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    application.bot_data["trade_service"] = TradeService(repo, notifier)
    application.bot_data["report_service"] = ReportService(repo)
    application.bot_data["analytics_service"] = AnalyticsService(repo)

    # تسجيل جميع المعالجات (الأوامر، المحادثات، الأزرار)
    register_all_handlers(application)
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
                allowed_updates=Update.ALL_TYPES
            )
            logging.info(f"Telegram webhook set to: {settings.TELEGRAM_WEBHOOK_URL}")
        else:
            logging.warning("TELEGRAM_WEBHOOK_URL not set; webhook not configured.")

    @app.on_event("shutdown")
    async def on_shutdown():
        if settings.TELEGRAM_WEBHOOK_URL:
            try:
                await ptb_app.bot.delete_webhook()
            except Exception:
                pass
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

# --- API Endpoints ---
# أبقِ بقية Endpoints كما هي في مشروعك
app.include_router(auth_router.router)
# --- END OF FILE ---