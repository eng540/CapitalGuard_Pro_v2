#--- START OF FILE: src/capitalguard/interfaces/api/main.py ---
import logging
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, PicklePersistence
from capitalguard.config import settings
from capitalguard.boot import build_services
from capitalguard.interfaces.telegram.handlers import register_all_handlers

app = FastAPI(title="CapitalGuard Pro API", version="5.1.0")

# --- Composition Root ---
services = build_services()

def create_ptb_app() -> Application:
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    
    application.bot_data["services"] = services
    
    register_all_handlers(application)
    return application

# --- Telegram Webhook Setup ---
ptb_app: Application | None = None
if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = create_ptb_app()

    @app.on_event("startup")
    async def on_startup():
        if ptb_app:
            await ptb_app.initialize()
            ptb_app.bot_data["services"] = services # Re-inject after persistence loads
            
            alert_service = services.get("alert_service")
            if alert_service:
                alert_service.schedule_job(ptb_app, interval_sec=60)
            
            if settings.TELEGRAM_WEBHOOK_URL:
                await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
                logging.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")

    @app.on_event("shutdown")
    async def on_shutdown():
        if ptb_app:
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

@app.get("/")
def root():
    return {"message": f"🚀 CapitalGuard API v{app.version} is running"}

# You can add your API endpoints here later, consuming from `services`
# For example: app.state.services = services
# Then in an endpoint: request.app.state.services["trade_service"]
#--- END OF FILE ---