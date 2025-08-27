# --- START OF FILE: src/capitalguard/interfaces/api/main.py ---
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address

import sentry_sdk

from telegram import Update
from telegram.ext import Application

from capitalguard.config import settings
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.interfaces.api.schemas import (
    RecommendationIn, RecommendationOut, CloseIn, ReportOut
)
from capitalguard.interfaces.telegram.webhook_handlers import (
    register_bot_handlers,
    unauthorized_handler,
)

# ✅ نظام المصادقة/الصلاحيات الجديد
from capitalguard.interfaces.api.security.deps import require_roles, get_current_user
from capitalguard.interfaces.api.routers import auth as auth_router

# راوترات اختيارية (لا تُفشل التشغيل إن لم تُوجد)
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


app = FastAPI(title="CapitalGuard Pro API", version="2.1.0")

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

# ✅ Rate limiting (محليًا هنا بدل الاستيراد من deps)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

app.add_middleware(SlowAPIMiddleware)

# --- Domain services ---
repo = RecommendationRepository()
analytics = AnalyticsService(repo)
notifier = TelegramNotifier()
trade = TradeService(repo, notifier)
report = ReportService(repo)

# --- Telegram via Webhook ---
ptb_app: Application | None = None

if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    # ربط الخدمات بالهاندلرز
    register_bot_handlers(ptb_app, trade, report, analytics)

    @app.on_event("startup")
    async def _startup():
        # 1) تهيئة PTB (ضروري مع الويبهوك)
        await ptb_app.initialize()
        # 2) ضبط الويبهوك إن توفّر URL
        if settings.TELEGRAM_WEBHOOK_URL:
            await ptb_app.bot.set_webhook(
                settings.TELEGRAM_WEBHOOK_URL,
                allowed_updates=["message", "edited_message", "callback_query"],
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
            return JSONResponse({"detail": "invalid json"}, status_code=400)

        update = Update.de_json(data, ptb_app.bot)
        try:
            await ptb_app.process_update(update)
        except Exception as e:
            logging.exception("Telegram update processing failed: %s", e)
            # رد افتراضي حتى لا يعيد تيليجرام المحاولة بلا نهاية
            await unauthorized_handler(update, None)
        return {"status": "ok"}
else:
    logging.warning("TELEGRAM_BOT_TOKEN not set; Telegram webhook disabled.")

# --- API Endpoints ---

@app.get("/")
def root():
    return {"message": "🚀 CapitalGuard API is running on Railway"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/favicon.ico")
def favicon():
    # منع 404 المزعج في اللوج
    return {}

# ✅ حماية بالصلاحيات/المستخدم
@app.post(
    "/recommendations",
    response_model=RecommendationOut,
    dependencies=[Depends(require_roles({"analyst"}))],
)
def create_rec(request: Request, payload: RecommendationIn, user: dict = Depends(get_current_user)):
    try:
        rec = trade.create(
            asset=payload.asset,
            side=payload.side,
            entry=payload.entry,
            stop_loss=payload.stop_loss,
            targets=payload.targets,
            channel_id=int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None,
            user_id=user.get("sub"),  # هوية المستخدم من الـ JWT/مزود الهوية
            notes=getattr(payload, "notes", None),
        )
        return RecommendationOut.model_validate(rec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get(
    "/recommendations",
    response_model=list[RecommendationOut],
    dependencies=[Depends(get_current_user)],
)
def list_recs(request: Request, channel_id: int | None = None):
    items = trade.list_all(channel_id)
    return [RecommendationOut.model_validate(i) for i in items]

@app.post(
    "/recommendations/{rec_id}/close",
    response_model=RecommendationOut,
    dependencies=[Depends(require_roles({"analyst"}))],
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
    dependencies=[Depends(get_current_user)],
)
def get_report(request: Request, channel_id: int | None = None):
    cid = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None
    return report.summary(channel_id or cid)

@app.get(
    "/analytics",
    dependencies=[Depends(get_current_user)],
)
def get_analytics(request: Request, channel_id: int | None = None):
    """
    إحصائيات الأداء: عدد الصفقات المغلقة، نسبة النجاح، مجموع/متوسط PnL، أفضل وأسوأ صفقة.
    إذا لم يُرسل channel_id نستخدم TELEGRAM_CHAT_ID (إن توفر).
    """
    cid = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None
    summary = analytics.performance_summary(channel_id or cid)
    return summary

# --- Routers ---
if metrics_router:
    app.include_router(metrics_router)

# ملاحظة: tv_router عادة يملك تحقق توقيع/سر داخلاً؛ لا نضيف Depends هنا
if tv_router:
    app.include_router(tv_router)

# ✅ Router المصادقة (تسجيل الدخول/التجديد…)
app.include_router(auth_router.router)
# --- END OF FILE ---