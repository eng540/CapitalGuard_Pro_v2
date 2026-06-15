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

from capitalguard.config import settings
from capitalguard.boot import bootstrap_app, build_services
from capitalguard.interfaces.telegram.handlers import register_all_handlers
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.routers import webapp as webapp_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.market_data_service import MarketDataService

# ✅ NEW: Import auto_backup_loop for background execution in production
from capitalguard.infrastructure.db.backup_service import auto_backup_loop

log = logging.getLogger(__name__)

# --- Redis Persistence Implementation (Complete & Correct) ---

class RedisPersistence(BasePersistence):
    """
    [STEP-4A] PTB v21+ persistence كاملة — JSON بدلاً من pickle.

    لماذا JSON بدلاً من pickle:
        1. أمان: pickle من مصدر غير موثوق → RCE (Remote Code Execution).
           إذا تعرّض Redis للاختراق وحُقنت بيانات فيه، pickle.loads تُنفِّذها.
        2. استقرار: pickle يُكسَر عند إعادة تسمية الكلاسات أو تغيير البنية.
           JSON يبقى قابلاً للقراءة عبر أي إصدار من Python.
        3. تدقيق: JSON مقروء بشرياً — يمكن فحص redis-cli بدون أدوات خاصة.
        4. توافقية: JSON المعياري يعمل مع أي لغة أو أداة مراقبة.

    تحديات خاصة بـ PTB:
        - مفاتيح conversations هي tuples مثل (123456, 789) وليست strings.
          JSON لا يدعم tuple keys. الحل: نُحوِّلها لـ "123456:789" ونُعيدها.
        - ConversationHandler.END = -1 (integer) → محفوظ بشكل طبيعي.
        - callback_data: بنية PTB الداخلية قد تحتوي بيانات غير JSON-serializable.
          الحل: نُحوِّل الأنواع غير المدعومة → str في _encode.

    خطة Migration عند أول تشغيل:
        Redis يحتوي بيانات pickle قديمة (تبدأ بـ \x80).
        _decode يتعامل معها بأمان: يُرجع {} بدلاً من الانهيار.
        البيانات الجديدة تُكتب بـ JSON تدريجياً.
    """

    # ── معرفات المفاتيح في Redis ─────────────────────────────────────────
    _KEY_USER_DATA     = "ptb:user_data"
    _KEY_CHAT_DATA     = "ptb:chat_data"
    _KEY_BOT_DATA      = "ptb:bot_data"
    _KEY_CALLBACK_DATA = "ptb:callback_data"
    _KEY_CONVERSATIONS = "ptb:conversations"

    def __init__(self, redis_client: redis.Redis):
        super().__init__()
        self.redis_client    = redis_client
        self.user_data_key   = self._KEY_USER_DATA
        self.chat_data_key   = self._KEY_CHAT_DATA
        self.bot_data_key    = self._KEY_BOT_DATA
        self.callback_data_key = self._KEY_CALLBACK_DATA
        self.conversations_key = self._KEY_CONVERSATIONS

    # ── JSON Serialization ───────────────────────────────────────────────

    @staticmethod
    def _encode(obj: Any) -> bytes:
        """
        [STEP-4A] Python object → JSON bytes.
        يُحوِّل الأنواع غير المدعومة (Decimal, set, tuple) → str بأمان.
        """
        def _default(o: Any) -> Any:
            from decimal import Decimal as _Decimal
            if isinstance(o, _Decimal):
                return str(o)
            if isinstance(o, (set, frozenset)):
                return list(o)
            if isinstance(o, tuple):
                return list(o)
            return str(o)  # fallback — لا نرفع TypeError أبداً

        return json.dumps(obj, default=_default, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _decode(data: Optional[bytes], fallback: Any = None) -> Any:
        """
        [STEP-4A] JSON bytes → Python object.
        يتعامل بأمان مع:
            - None  → fallback
            - pickle قديم (يبدأ بـ \x80) → fallback + warning
            - JSON تالف → fallback + error log
        """
        if data is None:
            return fallback if fallback is not None else {}

        # كشف pickle القديم (magic byte \x80)
        if isinstance(data, (bytes, bytearray)) and data[:1] == b'\x80':
            log.warning(
                "[STEP-4A] RedisPersistence: detected legacy pickle data — "
                "discarding and returning empty. "
                "Data will be re-written as JSON on next update."
            )
            return fallback if fallback is not None else {}

        try:
            raw = data.decode("utf-8") if isinstance(data, bytes) else data
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.error(
                "[STEP-4A] RedisPersistence: JSON decode failed: %s — returning empty.", e
            )
            return fallback if fallback is not None else {}

    # ── Tuple Key Helpers (لـ conversations) ────────────────────────────

    @staticmethod
    def _tuple_to_str(key: Tuple) -> str:
        """(123, 456) → '123:456' """
        return ":".join(str(k) for k in key)

    @staticmethod
    def _str_to_tuple(key_str: str) -> Tuple:
        """'123:456' → (123, 456) """
        return tuple(int(p) for p in key_str.split(":"))

    # ── Bot Data ──────────────────────────────────────────────────────────

    async def get_bot_data(self) -> Dict[str, Any]:
        data = self.redis_client.get(self.bot_data_key)
        return self._decode(data, fallback={})

    async def update_bot_data(self, data: Dict[str, Any]) -> None:
        self.redis_client.set(self.bot_data_key, self._encode(data))

    async def refresh_bot_data(self, bot_data: Dict) -> None:
        fresh = await self.get_bot_data()
        bot_data.update(fresh)

    # ── Chat Data ─────────────────────────────────────────────────────────

    async def get_chat_data(self) -> Dict[int, Dict[str, Any]]:
        raw = self.redis_client.hgetall(self.chat_data_key)
        return {int(k): self._decode(v, fallback={}) for k, v in raw.items()}

    async def update_chat_data(self, chat_id: int, data: Dict[str, Any]) -> None:
        self.redis_client.hset(self.chat_data_key, str(chat_id), self._encode(data))

    async def drop_chat_data(self, chat_id: int) -> None:
        self.redis_client.hdel(self.chat_data_key, str(chat_id))

    async def refresh_chat_data(self, chat_id: int, chat_data: Dict) -> None:
        raw = self.redis_client.hget(self.chat_data_key, str(chat_id))
        if raw:
            fresh = self._decode(raw, fallback={})
            chat_data.update(fresh)

    # ── User Data ─────────────────────────────────────────────────────────

    async def get_user_data(self) -> Dict[int, Dict[str, Any]]:
        raw = self.redis_client.hgetall(self.user_data_key)
        return {int(k): self._decode(v, fallback={}) for k, v in raw.items()}

    async def update_user_data(self, user_id: int, data: Dict[str, Any]) -> None:
        self.redis_client.hset(self.user_data_key, str(user_id), self._encode(data))

    async def drop_user_data(self, user_id: int) -> None:
        self.redis_client.hdel(self.user_data_key, str(user_id))

    async def refresh_user_data(self, user_id: int, user_data: Dict) -> None:
        raw = self.redis_client.hget(self.user_data_key, str(user_id))
        if raw:
            fresh = self._decode(raw, fallback={})
            user_data.update(fresh)

    # ── Conversations ─────────────────────────────────────────────────────

    async def get_conversations(self, name: str) -> Dict:
        raw = self.redis_client.hget(self.conversations_key, name)
        decoded = self._decode(raw, fallback={})
        # JSON يحوِّل tuple keys → strings — نُعيدها tuples
        return {self._str_to_tuple(k): v for k, v in decoded.items()}

    async def update_conversation(
        self,
        name: str,
        key: Tuple[int, ...],
        new_state: Optional[object],
    ) -> None:
        conversations = await self.get_conversations(name)
        if new_state is None:
            conversations.pop(key, None)
        else:
            conversations[key] = new_state
        # نحوِّل tuple keys → strings قبل JSON serialization
        serializable = {self._tuple_to_str(k): v for k, v in conversations.items()}
        self.redis_client.hset(self.conversations_key, name, self._encode(serializable))

    # ── Callback Data ─────────────────────────────────────────────────────

    async def get_callback_data(self) -> Optional[Any]:
        data = self.redis_client.get(self.callback_data_key)
        return self._decode(data, fallback=None)

    async def update_callback_data(self, data: Any) -> None:
        if data:
            self.redis_client.set(self.callback_data_key, self._encode(data))
        else:
            self.redis_client.delete(self.callback_data_key)

    # ── Flush ─────────────────────────────────────────────────────────────

    async def flush(self) -> None:
        pass

# --- FastAPI Application ---

app = FastAPI(title="CapitalGuard Pro API", version="27.2-webapp") # ✅ Version Bump
app.state.ptb_app = None
app.state.services = None

# ✅ WEBAPP SUPPORT: Mount static files for WebApp
import pathlib as _pathlib
_THIS_FILE    = _pathlib.Path(__file__).resolve()          # .../interfaces/api/main.py
_STATIC_DIR   = _THIS_FILE.parent / "static"               # .../interfaces/api/static

# [STEP-6C] مسار مطلق — لا يعتمد على CWD
# قبل: directory="src/capitalguard/interfaces/api/static"  ← يفشل إذا CWD ≠ /app
# بعد: مسار مطلق محسوب من موقع الملف الحالي
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=context.error)

async def _alert_service_watchdog(services: dict) -> None:
    """
    [STEP-2B] يراقب AlertService bg thread ويُعيد تشغيله عند الموت.

    المشكلة التي يُعالجها:
        AlertService يعمل على daemon thread. إذا مات بأي استثناء غير متوقع،
        لا أحد يُعيد تشغيله. النتيجة: مراقبة الأسعار تتوقف بصمت،
        SL/TP لا تُنفَّذ، صفقات تبقى مفتوحة بلا حماية.

    المعيار المالي (High Availability):
        في أنظمة التداول، خدمة مراقبة الأسعار تُصنَّف كـ critical service.
        يجب أن تكون highly available مع آلية self-healing تلقائية.

    استراتيجية Circuit Breaker:
        - فحص كل 15 ثانية
        - grace period 5 ثوانٍ قبل restart (تجنب restart loop)
        - حد أقصى 5 restarts/ساعة (Circuit Breaker)
        - بعد فتح الـ Circuit: انتظار ساعة كاملة قبل المحاولة مجدداً
    """
    import time as _time

    MAX_RESTARTS_PER_HOUR = 5
    RESTART_WINDOW_SEC    = 3600
    WATCHDOG_INTERVAL_SEC = 15
    GRACE_PERIOD_SEC      = 5

    restart_timestamps: list = []
    log.info("[STEP-2B] AlertService Watchdog running (interval=%ds).", WATCHDOG_INTERVAL_SEC)

    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_SEC)

            alert_svc = services.get("alert_service")
            if not alert_svc:
                continue

            thread_alive = (
                alert_svc._bg_thread is not None
                and alert_svc._bg_thread.is_alive()
            )
            if thread_alive:
                continue

            # ── thread مات ────────────────────────────────────────────────
            now = _time.monotonic()
            log.critical(
                "🚨 [WATCHDOG] AlertService bg thread is DEAD. "
                "Price monitoring STOPPED. Attempting restart..."
            )

            # تنظيف النافذة الزمنية
            restart_timestamps = [
                t for t in restart_timestamps if now - t < RESTART_WINDOW_SEC
            ]

            # فحص Circuit Breaker
            if len(restart_timestamps) >= MAX_RESTARTS_PER_HOUR:
                log.critical(
                    "🚨 [WATCHDOG] CIRCUIT BREAKER OPEN — %d restarts in last hour. "
                    "Manual intervention required. Waiting %d minutes before retry.",
                    len(restart_timestamps), RESTART_WINDOW_SEC // 60,
                )
                await asyncio.sleep(RESTART_WINDOW_SEC)
                restart_timestamps.clear()
                continue

            # ── إعادة التشغيل ──────────────────────────────────────────────
            await asyncio.sleep(GRACE_PERIOD_SEC)

            try:
                alert_svc.start()
                await asyncio.sleep(3)  # انتظر يستقر

                if alert_svc._bg_thread and alert_svc._bg_thread.is_alive():
                    await alert_svc.build_triggers_index()
                    restart_timestamps.append(now)
                    log.warning(
                        "✅ [WATCHDOG] AlertService restarted successfully. "
                        "Restart count this hour: %d/%d. Index rebuilt.",
                        len(restart_timestamps), MAX_RESTARTS_PER_HOUR,
                    )
                else:
                    log.critical(
                        "🚨 [WATCHDOG] AlertService restart FAILED — thread still dead."
                    )
            except Exception:
                log.exception("🚨 [WATCHDOG] Exception during AlertService restart.")

        except asyncio.CancelledError:
            log.info("[WATCHDOG] AlertService Watchdog cancelled — shutting down.")
            break
        except Exception:
            log.exception("[WATCHDOG] Unexpected error in watchdog loop — continuing.")


@app.on_event("startup")
async def on_startup():
    log.info("🚀 Application startup sequence initiated...")

    # ✅ START AUTO-BACKUP TASK FOR PRODUCTION
    log.info("Starting Auto-Backup background task for Production environment...")
    asyncio.create_task(auto_backup_loop())

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        log.critical("FATAL: REDIS_URL environment variable not found. Startup aborted.")
        return

    try:
        redis_client = redis.from_url(redis_url, decode_responses=False)
        redis_client.ping()
        persistence = RedisPersistence(redis_client=redis_client)
        log.info("✅ Connected to Redis for persistence.")
    except Exception as e:
        log.critical(f"FATAL: Could not connect to Redis: {e}. Startup aborted.")
        return

    # CRITICAL FIX: Correctly clear all persisted conversation states.
    log.warning("Clearing all persisted conversation states to ensure a clean start...")
    redis_client.delete(persistence.conversations_key)
    log.info("All conversation states have been cleared from persistence.")

    ptb_app = bootstrap_app(persistence=persistence)
    if not ptb_app:
        log.critical("FATAL: Could not create Telegram Application. Startup aborted.")
        return

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
    if alert_service:
        await alert_service.build_triggers_index()
        alert_service.start()
        log.info("AlertService background tasks started.")

        # ── [STEP-2B] AlertService Watchdog ──────────────────────────────────
        asyncio.create_task(
            _alert_service_watchdog(app.state.services),
            name="alertservice-watchdog",
        )
        log.info("[STEP-2B] AlertService Watchdog started.")

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

@app.get("/health", tags=["System"])
async def health_check(request: Request):
    """
    [STEP-2A] فحص صحة شامل وصادق.

    يفحص كل مكون حيوي ويُرجع حالته الفعلية.
    يُرجع 200 إذا النظام يعمل أو متدهور جزئياً.
    يُرجع 503 إذا مكوّن حيوي متوقف تماماً.

    المعيار المالي:
        Railway/Kubernetes يعتمدان على هذا الـ endpoint لـ restart decisions.
        /health يكذب = لا restart عند موت AlertService = صفقات غير مُغلقة.

    المكونات المفحوصة:
        database      — حيوي: لا يعمل بدونه
        alert_service — حيوي: مراقبة الأسعار والـ SL/TP
        price_feed    — مهم: استقبال تيكات Binance
        redis         — مهم: Pub/Sub والـ persistence
        telegram_bot  — حيوي: استقبال الأوامر وإرسال الإشعارات
    """
    import time
    from datetime import datetime, timezone
    from fastapi.responses import JSONResponse
    from sqlalchemy import text as sa_text
    from capitalguard.infrastructure.db.uow import engine

    t_start = time.monotonic()
    checks: Dict[str, Any] = {}
    is_critical_down = False

    # ── 1. قاعدة البيانات ─────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            conn.execute(sa_text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "down", "error": str(e)[:120]}
        is_critical_down = True

    # ── 2. AlertService ───────────────────────────────────────────────
    try:
        svcs = getattr(request.app.state, "services", None) or {}
        alert_svc = svcs.get("alert_service")
        if alert_svc is None:
            checks["alert_service"] = {"status": "down", "reason": "not_initialized"}
            is_critical_down = True
        elif not (alert_svc._bg_thread and alert_svc._bg_thread.is_alive()):
            checks["alert_service"] = {"status": "down", "reason": "bg_thread_dead"}
            is_critical_down = True
        else:
            diag = alert_svc.get_diagnostics() if hasattr(alert_svc, "get_diagnostics") else {}
            checks["alert_service"] = {
                "status":           "ok",
                "active_triggers":  diag.get("active_triggers", "?"),
                "symbols_monitored":diag.get("active_symbols",  "?"),
                "dropped_ticks":    diag.get("dropped_ticks",   {}),
            }
    except Exception as e:
        checks["alert_service"] = {"status": "degraded", "error": str(e)[:120]}

    # ── 3. Price Feed (PriceStreamer) ─────────────────────────────────
    try:
        svcs = getattr(request.app.state, "services", None) or {}
        alert_svc = svcs.get("alert_service")
        streamer = getattr(alert_svc, "streamer", None) if alert_svc else None
        if streamer and getattr(streamer, "_running", False):
            checks["price_feed"] = {
                "status":             "ok",
                "subscribed_symbols": len(getattr(streamer, "_subscribed_symbols", set())),
            }
        else:
            checks["price_feed"] = {"status": "degraded", "reason": "streamer_not_running"}
    except Exception as e:
        checks["price_feed"] = {"status": "degraded", "error": str(e)[:120]}

    # ── 4. Redis ──────────────────────────────────────────────────────
    try:
        redis_url = os.environ.get("REDIS_URL")
        if redis_url:
            import redis as redis_sync
            r = redis_sync.from_url(redis_url, socket_connect_timeout=2)
            r.ping()
            checks["redis"] = {"status": "ok"}
        else:
            checks["redis"] = {"status": "degraded", "reason": "REDIS_URL_not_set"}
    except Exception as e:
        checks["redis"] = {"status": "degraded", "error": str(e)[:120]}

    # ── 5. Telegram Bot ───────────────────────────────────────────────
    try:
        ptb = getattr(request.app.state, "ptb_app", None)
        if ptb and getattr(ptb, "bot", None):
            checks["telegram_bot"] = {"status": "ok"}
        else:
            checks["telegram_bot"] = {"status": "down", "reason": "not_initialized"}
            is_critical_down = True
    except Exception as e:
        checks["telegram_bot"] = {"status": "degraded", "error": str(e)[:120]}

    # ── مجموع الحالة ─────────────────────────────────────────────────
    all_ok = all(v.get("status") == "ok" for v in checks.values())
    if is_critical_down:
        overall = "down"
    elif all_ok:
        overall = "ok"
    else:
        overall = "degraded"

    body = {
        "status":           overall,
        "checks":           checks,
        "response_ms":      round((time.monotonic() - t_start) * 1000, 1),
        "timestamp_utc":    datetime.now(timezone.utc).isoformat(),
    }
    http_status = 503 if is_critical_down else 200
    return JSONResponse(content=body, status_code=http_status)

# ✅ WEBAPP SUPPORT: Include WebApp router
app.include_router(auth_router.router)
app.include_router(webapp_router.router)
app.include_router(metrics_router)

@app.get("/dash")
async def serve_dashboard():
    return FileResponse(str(_STATIC_DIR / "signal_dashboard.html"))  # [STEP-6C]

@app.get("/new")
async def serve_creator():
    return FileResponse(str(_STATIC_DIR / "create_trade.html"))  # [STEP-6C]

@app.get("/portfolio")
async def serve_portfolio():
    return FileResponse(str(_STATIC_DIR / "my_portfolio.html"))  # [STEP-6C]
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/api/main.py ---