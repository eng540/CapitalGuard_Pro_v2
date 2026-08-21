#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/api/main.py ---
# File: src/capitalguard/interfaces/api/main.py
# Version: v27.2 - Webapp Portfolio Shortcut & Auto-Backup
# ✅ THE FIX: Added Auto-Backup loop to FastAPI startup event for production.

import logging
import asyncio
import os
import html
import json
import traceback
from typing import List, Dict, Any, Optional, Tuple

import redis
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from telegram import Update, BotCommand
from telegram.ext import Application, ContextTypes, BasePersistence

from capitalguard.config import settings, validate_r5_noncommercial_controls
from capitalguard.boot import bootstrap_app, build_services
from capitalguard.interfaces.telegram.handlers import register_all_handlers
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.routers import webapp as webapp_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.interfaces.webhook.tradingview import router as tradingview_router
from capitalguard.interfaces.api.security.auth import validate_security_settings
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.market_data_service import MarketDataService

# ✅ NEW: Import auto_backup_loop for background execution in production
from capitalguard.infrastructure.db.backup_service import auto_backup_loop

log = logging.getLogger(__name__)


class _PersistenceCodec:
    """JSON codec for PTB persistence data; deliberately rejects unknown types."""

    @staticmethod
    def encode(value):
        if isinstance(value, dict):
            return {
                "__type__": "dict",
                "items": [[_PersistenceCodec.encode(k), _PersistenceCodec.encode(v)] for k, v in value.items()],
            }
        if isinstance(value, tuple):
            return {"__type__": "tuple", "items": [_PersistenceCodec.encode(v) for v in value]}
        if isinstance(value, set):
            return {"__type__": "set", "items": [_PersistenceCodec.encode(v) for v in value]}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError(f"Unsupported persistence type: {type(value).__name__}")

    @staticmethod
    def decode(value):
        if isinstance(value, list):
            return [_PersistenceCodec.decode(v) for v in value]
        if not isinstance(value, dict):
            return value
        marker = value.get("__type__")
        if marker == "dict":
            return {
                _PersistenceCodec.decode(k): _PersistenceCodec.decode(v)
                for k, v in value.get("items", [])
            }
        if marker == "tuple":
            return tuple(_PersistenceCodec.decode(v) for v in value.get("items", []))
        if marker == "set":
            return set(_PersistenceCodec.decode(v) for v in value.get("items", []))
        raise ValueError("Unknown persistence payload type")


def _dump_persistence(value) -> bytes:
    return json.dumps(_PersistenceCodec.encode(value), separators=(",", ":")).encode("utf-8")


def _load_persistence(data: bytes):
    return _PersistenceCodec.decode(json.loads(data.decode("utf-8")))


# --- Redis Persistence Implementation (Complete & Correct) ---

class RedisPersistence(BasePersistence):
    """A complete and PTB v21+ compatible persistence class that stores bot data in Redis."""

    def __init__(self, redis_client: redis.Redis):
        super().__init__()
        self.redis_client = redis_client
        # v2 namespace prevents accidentally decoding legacy pickle payloads.
        prefix = "ptb:v2:"
        self.user_data_key = f"{prefix}user_data"
        self.chat_data_key = f"{prefix}chat_data"
        self.bot_data_key = f"{prefix}bot_data"
        self.callback_data_key = f"{prefix}callback_data"
        self.conversations_key = f"{prefix}conversations"

    async def get_bot_data(self) -> Dict[str, Any]:
        data = self.redis_client.get(self.bot_data_key)
        return _load_persistence(data) if data else {}

    async def update_bot_data(self, data: Dict[str, Any]) -> None:
        self.redis_client.set(self.bot_data_key, _dump_persistence(data))

    async def get_chat_data(self) -> Dict[int, Dict[str, Any]]:
        data = self.redis_client.hgetall(self.chat_data_key)
        return {int(k): _load_persistence(v) for k, v in data.items()}

    async def update_chat_data(self, chat_id: int, data: Dict[str, Any]) -> None:
        self.redis_client.hset(self.chat_data_key, str(chat_id), _dump_persistence(data))

    async def get_user_data(self) -> Dict[int, Dict[str, Any]]:
        data = self.redis_client.hgetall(self.user_data_key)
        return {int(k): _load_persistence(v) for k, v in data.items()}

    async def update_user_data(self, user_id: int, data: Dict[str, Any]) -> None:
        self.redis_client.hset(self.user_data_key, str(user_id), _dump_persistence(data))

    async def get_conversations(self, name: str) -> Dict:
        data = self.redis_client.hget(self.conversations_key, name)
        return _load_persistence(data) if data else {}

    async def update_conversation(self, name: str, key: Tuple[int, ...], new_state: Optional[object]) -> None:
        conversations = await self.get_conversations(name)
        if new_state is None:
            conversations.pop(key, None)
        else:
            conversations[key] = new_state
        self.redis_client.hset(self.conversations_key, name, _dump_persistence(conversations))

    async def drop_chat_data(self, chat_id: int) -> None:
        self.redis_client.hdel(self.chat_data_key, str(chat_id))

    async def drop_user_data(self, user_id: int) -> None:
        self.redis_client.hdel(self.user_data_key, str(user_id))

    async def get_callback_data(self) -> Optional[Any]:
        data = self.redis_client.get(self.callback_data_key)
        return _load_persistence(data) if data else None

    async def update_callback_data(self, data: Any) -> None:
        if data:
            self.redis_client.set(self.callback_data_key, _dump_persistence(data))
        else:
            self.redis_client.delete(self.callback_data_key)

    async def refresh_bot_data(self, bot_data: Dict) -> None:
        data = await self.get_bot_data()
        bot_data.update(data)

    async def refresh_chat_data(self, chat_id: int, chat_data: Dict) -> None:
        data = self.redis_client.hget(self.chat_data_key, str(chat_id))
        if data:
            chat_data.update(_load_persistence(data))

    async def refresh_user_data(self, user_id: int, user_data: Dict) -> None:
        data = self.redis_client.hget(self.user_data_key, str(user_id))
        if data:
            user_data.update(_load_persistence(data))

    async def flush(self) -> None:
        pass

# --- FastAPI Application ---

app = FastAPI(title="CapitalGuard Pro API", version="27.2-webapp") # ✅ Version Bump
app.state.ptb_app = None
app.state.services = None
app.state.background_tasks: set[asyncio.Task] = set()
app.state.ready = False

# ✅ WEBAPP SUPPORT: Mount static files for WebApp
app.mount("/static", StaticFiles(directory="src/capitalguard/interfaces/api/static"), name="static")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=context.error)

@app.on_event("startup")
async def on_startup():
    log.info("🚀 Application startup sequence initiated...")

    validate_security_settings()
    r5_controls = validate_r5_noncommercial_controls()
    log.info(
        "R5 noncommercial controls verified: billing=%s copy_trading=%s auto_trade=%s trade_live=%s",
        r5_controls["billing_enabled"],
        r5_controls["copy_trading_enabled"],
        r5_controls["auto_trade_enabled"],
        r5_controls["trade_live_enabled"],
    )

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL is required; refusing to start without Redis.")

    try:
        redis_client = redis.from_url(redis_url, decode_responses=False)
        redis_client.ping()
        persistence = RedisPersistence(redis_client=redis_client)
        log.info("✅ Connected to Redis for persistence.")
    except Exception as e:
        raise RuntimeError("Could not connect to Redis; refusing to start.") from e

    # CRITICAL FIX: Correctly clear all persisted conversation states.
    log.warning("Clearing all persisted conversation states to ensure a clean start...")
    redis_client.delete(persistence.conversations_key)
    log.info("All conversation states have been cleared from persistence.")

    ptb_app = bootstrap_app(persistence=persistence)
    if not ptb_app:
        raise RuntimeError("Could not create Telegram Application; refusing to start.")

    app.state.ptb_app = ptb_app
    await ptb_app.initialize()
    log.info("Telegram app initialized and Redis data loaded.")

    app.state.services = build_services(ptb_app=ptb_app)
    ptb_app.bot_data["services"] = app.state.services
    register_all_handlers(ptb_app)
    ptb_app.add_error_handler(error_handler)

    # --- ✅ GEO-BLOCK FIX: Populate symbol cache *before* starting alert service ---
    market_data_service: MarketDataService = app.state.services.get("market_data_service")
    if market_data_service:
        log.info("Populating symbol cache (MarketDataService)...")
        await market_data_service.refresh_symbols_cache()
        log.info("Symbol cache population complete.")
        # ✅ P4-FIX: Circuit Breaker auto-recovery loop
        # يُعيد محاولة Binance تلقائياً بعد cooldown (30 دقيقة افتراضياً)
        # لا يستهلك موارد — ينام حتى ينتهي الـ cooldown
        asyncio.create_task(market_data_service._auto_refresh_loop())
        log.info("MarketDataService auto-refresh loop started.")
    else:
        log.error("MarketDataService not found, cache will not be populated on startup.")
    # --- End of Fix ---

    alert_service: AlertService = app.state.services.get("alert_service")
    if not alert_service:
        raise RuntimeError("AlertService is required; refusing to start without it.")
    await alert_service.build_triggers_index()
    alert_service.start()
    log.info("AlertService background tasks started.")

    publication_outbox = app.state.services.get("publication_outbox_service")
    if publication_outbox:
        await publication_outbox.start()
        log.info("PublicationOutbox worker started.")

    # Start recurring work only after all critical dependencies are ready.
    backup_task = asyncio.create_task(auto_backup_loop(), name="auto-backup")
    app.state.background_tasks.add(backup_task)
    backup_task.add_done_callback(app.state.background_tasks.discard)

    private_commands = [
        BotCommand("newrec", "📊 New Recommendation"),
        BotCommand("myportfolio", "📂 View My Trades"),
        BotCommand("help", "ℹ️ Show Help"),
    ]
    await ptb_app.bot.set_my_commands(private_commands)
    log.info("Bot commands configured.")

    if settings.TELEGRAM_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        log.info(f"Webhook set to {settings.TELEGRAM_WEBHOOK_URL}")

    await ptb_app.start()
    log.info("Telegram bot started.")
    if ptb_app.bot:
        log.info(f"✅ Bot is running as @{ptb_app.bot.username}")

    app.state.ready = True
    log.info("🚀 Application startup sequence complete.")

@app.on_event("shutdown")
async def on_shutdown():
    log.info("🔌 Application shutdown sequence initiated...")
    app.state.ready = False
    for task in list(app.state.background_tasks):
        task.cancel()
    if app.state.background_tasks:
        await asyncio.gather(*app.state.background_tasks, return_exceptions=True)
    alert_service: AlertService | None = (
        app.state.services.get("alert_service") if app.state.services else None
    )
    if alert_service:
        alert_service.stop()
        log.info("AlertService stopped.")
    publication_outbox = app.state.services.get("publication_outbox_service") if app.state.services else None
    if publication_outbox:
        await publication_outbox.stop()
        log.info("PublicationOutbox worker stopped.")
    if app.state.ptb_app:
        await app.state.ptb_app.stop()
        await app.state.ptb_app.shutdown()
        log.info("Telegram app shut down.")
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
    """Readiness endpoint: never reports healthy before startup is complete."""
    from fastapi import HTTPException

    if not app.state.ready or not app.state.ptb_app or not app.state.services:
        raise HTTPException(status_code=503, detail="Service is not ready")
    return {"status": "ok"}

# ✅ WEBAPP SUPPORT: Include WebApp router
app.include_router(auth_router.router)
app.include_router(webapp_router.router)
app.include_router(metrics_router)
app.include_router(tradingview_router)

@app.get("/dash")
async def serve_dashboard():
    return FileResponse("src/capitalguard/interfaces/api/static/signal_dashboard.html")

@app.get("/new")
async def serve_creator():
    return FileResponse("src/capitalguard/interfaces/api/static/create_trade.html")

@app.get("/portfolio")
async def serve_portfolio():
    return FileResponse("src/capitalguard/interfaces/api/static/my_portfolio.html")
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/api/main.py ---
