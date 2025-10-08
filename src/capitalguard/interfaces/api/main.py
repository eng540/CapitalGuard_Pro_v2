# src/capitalguard/interfaces/api/main.py (v25.0 - FINAL & ROBUST STARTUP)
"""
The main entry point for the FastAPI application.
This file is responsible for bootstrapping the entire application, including the
Telegram bot and background services, during the FastAPI startup event.
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
from capitalguard.boot import bootstrap_app, build_services # Corrected imports
from capitalguard.interfaces.api.deps import get_trade_service, get_analytics_service, require_api_key
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.infrastructure.db.base import get_session

log = logging.getLogger(__name__)

# --- Application Setup ---

app = FastAPI(title="CapitalGuard Pro API", version="25.0.0-stable")
app.state.ptb_app = None
app.state.services = None

# --- Global Telegram Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs all uncaught exceptions from handlers and notifies the admin."""
    log.error("Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    detailed_message = (
        f"An exception was raised while handling an update\n\n"
        f"<b>Update:</b>\n<pre>{html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))[:3500]}</pre>\n\n"
        f"<b>Error:</b>\n<pre>{html.escape(tb_string)}</pre>"
    )

    if settings.TELEGRAM_ADMIN_CHAT_ID and app.state.ptb_app:
        try:
            await app.state.ptb_app.bot.send_message(
                chat_id=settings.TELEGRAM_ADMIN_CHAT_ID, text=detailed_message, parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.error(f"CRITICAL: Failed to send detailed error report to admin: {e}")

    if update and getattr(update, "effective_user", None):
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="⚠️ Sorry, an internal error occurred. The development team has been notified.",
            )
        except Exception as e:
            log.error(f"Failed to send error notification to user {update.effective_user.id}: {e}")


# --- Startup / Shutdown Events ---
@app.on_event("startup")
async def on_startup():
    """
    Handles application startup logic for FastAPI, Telegram Bot, and background services.
    This new sequence is robust and ensures all components are initialized correctly.
    """
    log.info("🚀 Application startup sequence initiated...")

    # Step 1: Bootstrap the Telegram Application instance. This is lightweight.
    ptb_app = bootstrap_app()
    if not ptb_app:
        log.critical("FATAL: Could not create Telegram Application. Bot features will be disabled.")
        # Even without the bot, we might want the API to run, so we build services without it.
        app.state.services = build_services(ptb_app=None)
        return

    app.state.ptb_app = ptb_app
    
    # Step 2: Build all application services and inject the ptb_app into them.
    # This populates ptb_app.bot_data and makes services available.
    app.state.services = build_services(ptb_app=ptb_app)
    log.info("✅ All application services built and registered.")

    # Step 3: Add the global error handler to the application.
    ptb_app.add_error_handler(error_handler)

    # Step 4: Start background services that need to run continuously.
    market_data_service = app.state.services.get("market_data_service")
    if market_data_service:
        asyncio.create_task(market_data_service.refresh_symbols_cache())
        log.info("Market data cache refresh task scheduled.")

    alert_service: AlertService = app.state.services.get("alert_service")
    if alert_service:
        await alert_service.build_triggers_index()
        alert_service.start()
        log.info("AlertService background tasks started.")

    # Step 5: Fully initialize the Telegram application. This connects to Telegram APIs.
    await ptb_app.initialize()
    log.info("Telegram application initialized.")

    # Step 6: Set bot commands and webhook after initialization.
    private_commands = [
        BotCommand("newrec", "📊 New Recommendation"),
        BotCommand("myportfolio", "📂 View My Trades"),
        BotCommand("help", "ℹ️ Show Help"),
    ]
    await ptb_app.bot.set_my_commands(private_commands)
    log.info("Custom bot commands have been set.")

    if settings.TELEGRAM_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(
            url=settings.TELEGRAM_WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES
        )
        log.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")
    
    # Step 7: Start the PTB application's main loop.
    await ptb_app.start()
    log.info("Telegram application polling/webhook handler started.")
    
    if ptb_app.bot:
        log.info(f"✅ Bot is running as @{ptb_app.bot.username}")
    
    log.info("🚀 Application startup sequence complete.")


@app.on_event("shutdown")
async def on_shutdown():
    """Handles graceful shutdown for the Telegram bot and background services."""
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


# --- Webhook Endpoint ---
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Single endpoint for receiving all updates from the Telegram webhook."""
    ptb_app = request.app.state.ptb_app
    if ptb_app:
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception:
            log.exception("Error processing Telegram update in webhook.")
    return {"status": "ok"}


# --- API Endpoints ---
@app.get("/")
def root():
    return {"message": f"🚀 CapitalGuard API v{app.version} is running"}

@app.get("/health", status_code=200, tags=["System"])
def health_check():
    # A more advanced health check could query the DB or check background task health
    return {"status": "ok"}

@app.get("/recommendations", response_model=List[RecommendationOut], dependencies=[Depends(require_api_key)])
def list_recommendations(
    db: Session = Depends(get_session),
    trade_service: TradeService = Depends(get_trade_service),
    symbol: str = Query(None),
    status: str = Query(None)
):
    items = trade_service.repo.list_all(db, symbol=symbol, status=status)
    return [RecommendationOut.from_orm(item) for item in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_api_key)])
async def close_recommendation(
    rec_id: int,
    payload: CloseIn,
    db: Session = Depends(get_session),
    trade_service: TradeService = Depends(get_trade_service)
):
    try:
        rec_orm = trade_service.repo.get(db, rec_id)
        if not rec_orm or not rec_orm.analyst:
            raise ValueError("Recommendation not found or has no associated user.")
        
        # We need the user's telegram ID for the service method
        user_telegram_id = str(rec_orm.analyst.telegram_user_id)
        
        closed_rec = await trade_service.close_recommendation_for_user_async(
            rec_id, user_telegram_id, payload.exit_price, db_session=db
        )
        return RecommendationOut.from_orm(closed_rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error(f"Failed to close recommendation via API: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")

# --- Include Routers ---
app.include_router(auth_router.router)
app.include_router(metrics_router)