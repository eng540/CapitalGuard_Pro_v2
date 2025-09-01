#--- START OF FILE: src/capitalguard/interfaces/api/main.py ---
import logging
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, PicklePersistence
from capitalguard.config import settings
from capitalguard.boot import build_services
from capitalguard.interfaces.telegram.handlers import register_all_handlers

app = FastAPI(title="CapitalGuard Pro API", version="5.2.0")

# --- Composition Root ---
# إنشاء جميع الخدمات مرة واحدة عند بدء تشغيل التطبيق
services = build_services()

def create_ptb_app() -> Application:
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()

    # حقن حزمة الخدمات الموحدة في bot_data
    application.bot_data["services"] = services

    register_all_handlers(application)
    return application

# --- Telegram Webhook Setup ---
ptb_app: Application | None = None
if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = create_ptb_app()

    @app.on_event("startup")
    async def on_startup():
        await ptb_app.initialize()
        
        # ✅ تفعيل خدمة التنبيهات وجدولتها لتعمل كل 60 ثانية
        alert_service = services["alert_service"]
        alert_service.schedule_job(ptb_app, interval_sec=60)
        
        if settings.TELEGRAM_WEBHOOK_URL:
            await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
            logging.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")

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

@app.get("/")
def root():
    return {"message": "🚀 CapitalGuard API v5.2 is running"}

# --- API Endpoints ---
# يمكنك إضافة نقاط نهاية API هنا لاحقًا لتستهلك الخدمات من `services`
# مثال:
# @app.get("/api/status")
# def api_status():
#     return {"status": "ok", "services": list(services.keys())}
#--- END OF FILE ---