# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/api/main.py ---
import logging
import os
import pickle
from typing import Dict, Any, Tuple, Optional

import redis
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from telegram import Update, BotCommand
from telegram.ext import Application, ContextTypes, BasePersistence

from capitalguard.config import settings
from capitalguard.boot import bootstrap_app, build_services
from capitalguard.interfaces.telegram.handlers import register_all_handlers
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.routers import webapp as webapp_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.market_data_service import MarketDataService

log = logging.getLogger(__name__)

# --- Redis Persistence ---
class RedisPersistence(BasePersistence):
    def __init__(self, redis_client: redis.Redis):
        super().__init__()
        self.redis_client = redis_client
        self.user_data_key = "ptb:user_data"
        self.chat_data_key = "ptb:chat_data"
        self.bot_data_key = "ptb:bot_data"
        self.callback_data_key = "ptb:callback_data"
        self.conversations_key = "ptb:conversations"

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

    async def drop_chat_data(self, chat_id: int) -> None:
        self.redis_client.hdel(self.chat_data_key, str(chat_id))

    async def drop_user_data(self, user_id: int) -> None:
        self.redis_client.hdel(self.user_data_key, str(user_id))

    async def get_callback_data(self) -> Optional[Any]:
        data = self.redis_client.get(self.callback_data_key)
        return pickle.loads(data) if data else None

    async def update_callback_data(self, data: Any) -> None:
        if data:
            self.redis_client.set(self.callback_data_key, pickle.dumps(data))
        else:
            self.redis_client.delete(self.callback_data_key)

    async def refresh_bot_data(self, bot_data: Dict) -> None:
        data = await self.get_bot_data()
        bot_data.update(data)

    async def refresh_chat_data(self, chat_id: int, chat_data: Dict) -> None:
        data = self.redis_client.hget(self.chat_data_key, str(chat_id))
        if data:
            chat_data.update(pickle.loads(data))

    async def refresh_user_data(self, user_id: int, user_data: Dict) -> None:
        data = self.redis_client.hget(self.user_data_key, str(user_id))
        if data:
            user_data.update(pickle.loads(data))

    async def flush(self) -> None:
        pass

# --- FastAPI App ---
app = FastAPI(title="CapitalGuard Pro API", version="70.0.0-WEB-READY")
app.state.ptb_app = None
app.state.services = None

# ✅ Mount Static Files
app.mount("/static", StaticFiles(directory="src/capitalguard/interfaces/api/static"), name="static")

# ✅ SHORTCUT ROUTES (Critical for Android WebApp)
@app.get("/portfolio")
async def serve_portfolio():
    return FileResponse("src/capitalguard/interfaces/api/static/portfolio.html")

@app.get("/new")
async def serve_creator():
    return FileResponse("src/capitalguard/interfaces/api/static/create_trade.html")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=context.error)

@app.on_event("startup")
async def on_startup():
    log.info("🚀 Application startup sequence initiated...")
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        log.critical("FATAL: REDIS_URL environment variable not found.")
        return

    try:
        redis_client = redis.from_url(redis_url, decode_responses=False)
        redis_client.ping()
        persistence = RedisPersistence(redis_client=redis_client)
        log.info("✅ Connected to Redis.")
    except Exception as e:
        log.critical(f"FATAL: Redis connection failed: {e}")
        return

    # Clear conversations on startup to prevent stuck states
    redis_client.delete(persistence.conversations_key)

    ptb_app = bootstrap_app(persistence=persistence)
    if not ptb_app:
        log.critical("FATAL: Could not create Telegram Application.")
        return

    app.state.ptb_app = ptb_app
    await ptb_app.initialize()

    app.state.services = build_services(ptb_app=ptb_app)
    ptb_app.bot_data["services"] = app.state.services
    register_all_handlers(ptb_app)
    ptb_app.add_error_handler(error_handler)

    # Populate Cache
    market_data_service: MarketDataService = app.state.services.get("market_data_service")
    if market_data_service:
        await market_data_service.refresh_symbols_cache()

    alert_service: AlertService = app.state.services.get("alert_service")
    if alert_service:
        await alert_service.build_triggers_index()
        alert_service.start()

    # Bot Commands
    await ptb_app.bot.set_my_commands([
        BotCommand("start", "🏠 Main Menu"),
        BotCommand("help", "ℹ️ Help"),
    ])

    if settings.TELEGRAM_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)

    await ptb_app.start()
    log.info("🚀 Application startup complete.")

@app.on_event("shutdown")
async def on_shutdown():
    alert_service: AlertService = app.state.services.get("alert_service")
    if alert_service: alert_service.stop()
    if app.state.ptb_app:
        await app.state.ptb_app.stop()
        await app.state.ptb_app.shutdown()

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    ptb_app = request.app.state.ptb_app
    if ptb_app:
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception:
            log.exception("Error processing update")
    return {"status": "ok"}

@app.get("/")
def root(): return {"message": "CapitalGuard API Running"}

@app.get("/health")
def health_check(): return {"status": "ok"}

app.include_router(auth_router.router)
app.include_router(metrics_router)
app.include_router(webapp_router.router)
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---