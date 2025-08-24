from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

# معدل الطلبات
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# رصد الأخطاء (اختياري)
import sentry_sdk

# Telegram Webhook Integration
from telegram import Update
from telegram.ext import Application
from capitalguard.interfaces.telegram.webhook_handlers import (
    register_bot_handlers, setup_telegram_webhook, shutdown_telegram_webhook
)

from capitalguard.config import settings
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.interfaces.api.schemas import (
    RecommendationIn, RecommendationOut, CloseIn, ReportOut
)
from capitalguard.interfaces.api.deps import limiter, require_api_key

# Routers الاختيارية
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


# --- تطبيق FastAPI ---
app = FastAPI(title="CapitalGuard Pro API", version="2.0.0")

# --- Sentry (اختياري) ---
if settings.SENTRY_DSN:
    sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.CORS_ORIGINS == "*" else settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Rate limiting (slowapi) ---
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

app.add_middleware(SlowAPIMiddleware)

# --- DI / Services ---
repo = RecommendationRepository()
notifier = TelegramNotifier()
trade = TradeService(repo, notifier)
report = ReportService(repo)

# --- Telegram Bot Webhook Integration ---
ptb_app: Application | None = None
if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    register_bot_handlers(ptb_app, trade, report)

    @app.post("/webhook/telegram")
    async def telegram_webhook(request: Request):
        # يحوّل تحديث تيليجرام ويعالج الأوامر
        update = Update.de_json(await request.json(), ptb_app.bot)
        await ptb_app.process_update(update)
        return {"status": "ok"}

    # إعداد/مسح الويب هوك عند بدء/إيقاف التطبيق
    app.add_event_handler("startup", setup_telegram_webhook(ptb_app))
    app.add_event_handler("shutdown", shutdown_telegram_webhook(ptb_app))
else:
    logging.warning("TELEGRAM_BOT_TOKEN is not set; Telegram webhook disabled.")

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

# --- Include optional routers ---
if metrics_router:
    app.include_router(metrics_router)
if tv_router:
    # حماية Webhook TradingView بمفتاح API
    from fastapi import Depends
    app.include_router(tv_router, dependencies=[Depends(require_api_key)])