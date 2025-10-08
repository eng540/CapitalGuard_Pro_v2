# src/capitalguard/interfaces/api/main.py (v25.1 - DECOUPLED STARTUP)
"""
The main entry point for the FastAPI application, with a decoupled startup sequence.
"""

import logging
import asyncio
import html
import json
import traceback
from typing import List

from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from capitalguard.config import settings
# ✅ **THE FIX:** Import handlers separately, not from boot.
from capitalguard.boot import bootstrap_app, build_services
from capitalguard.interfaces.telegram.handlers import register_all_handlers
from capitalguard.interfaces.api.deps import get_trade_service, get_analytics_service, require_api_key
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.infrastructure.db.base import get_session

log = logging.getLogger(__name__)

app = FastAPI(title="CapitalGuard Pro API", version="25.1.0-stable")
app.state.ptb_app = None
app.state.services = None

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=context.error)
    # ... (error handling logic remains the same)

@app.on_event("startup")
async def on_startup():
    log.info("🚀 Application startup sequence initiated...")

    ptb_app = bootstrap_app()
    if not ptb_app:
        log.critical("FATAL: Could not create Telegram Application. Bot features will be disabled.")
        app.state.services = build_services(ptb_app=None)
        return

    app.state.ptb_app = ptb_app
    app.state.services = build_services(ptb_app=ptb_app)
    ptb_app.bot_data["services"] = app.state.services
    log.info("✅ All application services built and registered.")

    # ✅ **THE FIX:** Register handlers AFTER services are built and attached.
    register_all_handlers(ptb_app)
    log.info("✅ All Telegram handlers registered.")

    ptb_app.add_error_handler(error_handler)

    market_data_service = app.state.services.get("market_data_service")
    if market_data_service:
        asyncio.create_task(market_data_service.refresh_symbols_cache())
        log.info("Market data cache refresh task scheduled.")

    alert_service: AlertService = app.state.services.get("alert_service")
    if alert_service:
        await alert_service.build_triggers_index()
        alert_service.start()
        log.info("AlertService background tasks started.")

    await ptb_app.initialize()
    log.info("Telegram application initialized.")

    private_commands = [
        BotCommand("newrec", "📊 New Recommendation"),
        BotCommand("myportfolio", "📂 View My Trades"),
        BotCommand("help", "ℹ️ Show Help"),
    ]
    await ptb_app.bot.set_my_commands(private_commands)
    log.info("Custom bot commands have been set.")

    if settings.TELEGRAM_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        log.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")
    
    await ptb_app.start()
    log.info("Telegram application polling/webhook handler started.")
    
    if ptb_app.bot:
        log.info(f"✅ Bot is running as @{ptb_app.bot.username}")
    
    log.info("🚀 Application startup sequence complete.")

# ... (The rest of the file: shutdown, endpoints, etc., remains the same)
@app.on_event("shutdown")
async def on_shutdown():
    log.info("🔌 Application shutdown sequence initiated...")
    alert_service: AlertService = app.state.services.get("alert_service")
    if alert_service:
        alert_service.stop()
        log.info("AlertService stopped.")
    if app.state.ptb_app:
        await app.state.ptb_app.stop()
        await app.state.ptb_app.shutdown()
        log.info("Telegram application shut down.")
    log.info("🔌 Application shutdown complete.")

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    ptb_app = request.app.state.ptb_app
    if ptb_app:
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception:
            log.exception("Error processing Telegram update in webhook.")
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": f"🚀 CapitalGuard API v{app.version} is running"}

@app.get("/health", status_code=200, tags=["System"])
def health_check():
    return {"status": "ok"}

app.include_router(auth_router.router)
app.include_router(metrics_router)

#END