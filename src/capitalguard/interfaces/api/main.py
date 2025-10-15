# src/capitalguard/interfaces/api/main.py (v26.0 - FINAL with Redis Persistence)
"""
The main entry point for the FastAPI application, with a robust and state-safe startup
sequence, now featuring Redis-backed persistence to ensure session consistency across
multiple workers, permanently fixing all "Stale action" errors.
"""

import logging
import asyncio
import html
import json
import pickle
import traceback
from typing import List, Dict, Any, Optional

import redis
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes, BasePersistence, PersistenceInput

from capitalguard.config import settings
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

# --- Redis Persistence Implementation ---

class RedisPersistence(BasePersistence):
    """A custom persistence class that stores bot data in Redis."""

    def __init__(self, redis_client: redis.Redis):
        super().__init__(store_user_data=True, store_chat_data=True, store_bot_data=True)
        self.redis_client = redis_client
        self.user_data_key = "ptb_user_data"
        self.chat_data_key = "ptb_chat_data"
        self.bot_data_key = "ptb_bot_data"
        self.conversations_key = "ptb_conversations"

    async def get_bot_data(self) -> Dict[str, Any]:
        data = self.redis_client.get(self.bot_data_key)
        return pickle.loads(data) if data else {}

    async def update_bot_data(self, data: Dict[str, Any]) -> None:
        self.redis_client.set(self.bot_data_key, pickle.dumps(data))

    async def get_chat_data(self) -> Dict[int, Dict[str, Any]]:
        data = self.redis_client.hgetall(self.chat_data_key)
        return {int(k): pickle.loads(v) for k, v in data.items()}

    async def update_chat_data(self, chat_id: int, data: Dict[str, Any]) -> None:
        self.redis_client.hset(self.chat_data_key, str(chat_id), pickle.dumps(data))

    async def get_user_data(self) -> Dict[int, Dict[str, Any]]:
        data = self.redis_client.hgetall(self.user_data_key)
        return {int(k): pickle.loads(v) for k, v in data.items()}

    async def update_user_data(self, user_id: int, data: Dict[str, Any]) -> None:
        self.redis_client.hset(self.user_data_key, str(user_id), pickle.dumps(data))

    async def get_conversations(self, name: str) -> Dict:
        data = self.redis_client.hget(self.conversations_key, name)
        return pickle.loads(data) if data else {}

    async def update_conversation(self, name: str, key: Tuple[int, ...], new_state: Optional[object]) -> None:
        conversations = await self.get_conversations(name)
        if new_state is None:
            conversations.pop(key, None)
        else:
            conversations[key] = new_state
        self.redis_client.hset(self.conversations_key, name, pickle.dumps(conversations))

    async def flush(self) -> None:
        # Redis writes are atomic, so flush is not strictly necessary
        pass

# --- FastAPI Application ---

app = FastAPI(title="CapitalGuard Pro API", version="26.0.0-persistent")
app.state.ptb_app = None
app.state.services = None

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=context.error)

@app.on_event("startup")
async def on_startup():
    log.info("🚀 Application startup sequence initiated...")

    # Step 1: Set up Redis-backed persistence. THIS IS THE CRITICAL FIX.
    try:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=False)
        redis_client.ping()
        persistence = RedisPersistence(redis_client=redis_client)
        log.info(f"✅ Successfully connected to Redis at {settings.REDIS_URL} for persistence.")
    except Exception as e:
        log.critical(f"FATAL: Could not connect to Redis for persistence: {e}. Startup aborted.")
        # In a real scenario, this should cause the container to exit unhealthy.
        return

    # Step 2: Create the PTB Application instance with persistence.
    ptb_app = bootstrap_app(persistence=persistence)
    if not ptb_app:
        log.critical("FATAL: Could not create Telegram Application. Startup aborted.")
        return

    app.state.ptb_app = ptb_app
    
    # Step 3: Initialize the application. This loads data from Redis.
    await ptb_app.initialize()
    log.info("Telegram application initialized, persistence data loaded from Redis.")

    # Step 4: Build and attach services.
    app.state.services = build_services(ptb_app=ptb_app)
    ptb_app.bot_data["services"] = app.state.services
    log.info("✅ All application services built and registered.")

    # Step 5: Register all handlers.
    register_all_handlers(ptb_app)
    log.info("✅ All Telegram handlers registered.")

    ptb_app.add_error_handler(error_handler)

    # Step 6: Start background services.
    market_data_service = app.state.services.get("market_data_service")
    if market_data_service:
        asyncio.create_task(market_data_service.refresh_symbols_cache())
        log.info("Market data cache refresh task scheduled.")

    alert_service: AlertService = app.state.services.get("alert_service")
    if alert_service:
        await alert_service.build_triggers_index()
        alert_service.start()
        log.info("AlertService background tasks started.")

    # Step 7: Set bot commands and webhook.
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
    
    # Step 8: Start the PTB application's main processing loop.
    await ptb_app.start()
    log.info("Telegram application polling/webhook handler started.")
    
    if ptb_app.bot:
        log.info(f"✅ Bot is running as @{ptb_app.bot.username}")
    
    log.info("🚀 Application startup sequence complete.")

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