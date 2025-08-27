from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

import sentry_sdk

from telegram import Update
from telegram.ext import Application

from capitalguard.config import settings
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.interfaces.api.schemas import (
    RecommendationIn, RecommendationOut, CloseIn, ReportOut
)
from capitalguard.interfaces.api.deps import limiter, require_api_key
from capitalguard.application.services.analytics_service import AnalyticsService

# Handlers
from capitalguard.interfaces.telegram.webhook_handlers import (
    register_bot_handlers,
    unauthorized_handler,
)

# Optional routers
metrics_router = None
tv_router = None
try:
    from capitalguard.interfaces.api.metrics import router as _metrics_router
    metrics_router = _metrics_router
except Exception:
    pass

try:
    from capitalguard.interfaces.webhook.tradingview import router as _tv_router
    tv_router = _tv_router
except Exception:
    pass


app = FastAPI(title="CapitalGuard Pro API", version="2.0.0")

# Sentry
if settings.SENTRY_DSN:
    sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.CORS_ORIGINS == "*" else settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

app.add_middleware(SlowAPIMiddleware)

# Domain services
repo = RecommendationRepository()
analytics = AnalyticsService(repo)
notifier = TelegramNotifier()
trade = TradeService(repo, notifier)
report = ReportService(repo)

# --- Telegram via Webhook ---
ptb_app: Application | None = None

if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    # سجل الأوامر + ربط الخدمات
    register_bot_handlers(ptb_app, trade, report, analytics)

    @app.on_event("startup")
    async def _startup():
        # 1) تهيئة تطبيق PTB (ضروري مع الويبهوك)
        await ptb_app.initialize()
        # 2) ضبط الويبهوك إذا تم توفير URL
        if settings.TELEGRAM_WEBHOOK_URL:
            await ptb_app.bot.set_webhook(
                settings.TELEGRAM_WEBHOOK_URL,
                allowed_updates=["message","edited_message","callback_query"]
            )
            logging.info(f"✅ Telegram webhook set to: {settings.TELEGRAM_WEBHOOK_URL}")
        else:
            logging.warning("⚠️ TELEGRAM_WEBHOOK_URL is not set. Telegram webhook not configured.")

    @app.on_event("shutdown")
    async def _shutdown():
        try:
            await ptb_app.bot.delete_webhook()
        except Exception:
            pass
        await ptb_app.shutdown()

    @app.post("/webhook/telegram")
    async def telegram_webhook(request: Request):
        """
        نقطة استقبال تحديثات تيليجرام. لا تضع RateLimit هنا.
        """
        try:
            data = await request.json()
        except Exception:
            # في حالات نادرة يجي بدون JSON
            return JSONResponse({"detail": "invalid json"}, status_code=400)

        update = Update.de_json(data, ptb_app.bot)
        try:
            await ptb_app.process_update(update)
        except Exception as e:
            # لا تسقط السيرفر حتى لو أخفق الهاندلر
            logging.exception("Telegram update processing failed: %s", e)
            # رُدّ 200 حتى لا يعيد تيليجرام المحاولة بلا نهاية
            await unauthorized_handler(update, None)  # رد افتراضي إن لزم
        return {"status": "ok"}

else:
    logging.warning("TELEGRAM_BOT_TOKEN not set; Telegram webhook disabled.")


# --- API Endpoints ---
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post(
    "/recommendations",
    response_model=RecommendationOut,
    dependencies=[Depends(require_api_key)],
)
def create_rec(request: Request, payload: RecommendationIn):
    try:
        rec = trade.create(
            asset=payload.asset,
            side=payload.side,
            entry=payload.entry,
            stop_loss=payload.stop_loss,
            targets=payload.targets,
            channel_id=int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None,
            user_id=payload.user_id,
            notes=getattr(payload, 'notes', None)
        )
        return RecommendationOut.model_validate(rec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get(
    "/recommendations",
    response_model=list[RecommendationOut],
    dependencies=[Depends(require_api_key)],
)
def list_recs(request: Request, channel_id: int | None = None):
    items = trade.list_all(channel_id)
    return [RecommendationOut.model_validate(i) for i in items]

@app.post(
    "/recommendations/{rec_id}/close",
    response_model=RecommendationOut,
    dependencies=[Depends(require_api_key)],
)
def close_rec(request: Request, rec_id: int, payload: CloseIn):
    try:
        rec = trade.close(rec_id, payload.exit_price)
        return RecommendationOut.model_validate(rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get(
    "/report",
    response_model=ReportOut,
    dependencies=[Depends(require_api_key)],
)
def get_report(request: Request, channel_id: int | None = None):
    cid = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None
    return report.summary(channel_id or cid)

# Optional routers
if metrics_router:
    app.include_router(metrics_router)
if tv_router:
    from fastapi import Depends
    app.include_router(tv_router, dependencies=[Depends(require_api_key)])
# ==== Basic root & health endpoints (no DB changes) ====

@app.get("/")
def root():
    return {"message": "🚀 CapitalGuard API is running on Railway"}

@app.get("/healthz")
def healthz():
    # أبسط فحص صحّة: التطبيق اشتغل ويستقبل الطلبات
    return {"status": "ok"}

@app.get("/favicon.ico")
def favicon():
    # منع 404 المزعج في اللوج
    return {}

@app.get(
    "/analytics",
    dependencies=[Depends(require_api_key)],
)
def get_analytics(request: Request, channel_id: int | None = None):
    """
    إحصائيات الأداء: عدد الصفقات المغلقة، نسبة النجاح، مجموع/متوسط PnL، أفضل وأسوأ صفقة.
    إذا لم يُرسل channel_id نستخدم TELEGRAM_CHAT_ID (إن توفر).
    """
    cid = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None
    summary = analytics.performance_summary(channel_id or cid)
    return summary