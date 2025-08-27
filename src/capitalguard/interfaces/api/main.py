#--- START OF FILE: src/capitalguard/interfaces/api/main.py ---
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
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.interfaces.api.schemas import RecommendationIn, RecommendationOut, CloseIn, ReportOut
from capitalguard.interfaces.telegram.webhook_handlers import register_bot_handlers, unauthorized_handler

# ✅ استيراد أدوات الحماية الجديدة
from capitalguard.interfaces.api.security.deps import require_roles, get_current_user
from capitalguard.interfaces.api.routers import auth as auth_router

# ... (Routers for metrics and tv can be imported as before) ...

app = FastAPI(title="CapitalGuard Pro API", version="2.1.0") # Version bump

# ... (Sentry, CORS, Rate Limiting setup remains the same) ...
# Sentry
if settings.SENTRY_DSN:
    sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.CORS_ORIGINS == "*" else settings.CORS_ORIGINS.split(","),
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Rate limiting (imports moved to the top)
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)
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
# ... (Telegram bot setup remains the same) ...

# --- API Endpoints ---
@app.get("/health")
async def health():
    return {"status": "ok"}

# ✅ تم تغيير الحماية من require_api_key إلى require_roles
@app.post(
    "/recommendations",
    response_model=RecommendationOut,
    dependencies=[Depends(require_roles({"analyst"}))],
)
def create_rec(request: Request, payload: RecommendationIn, user: dict = Depends(get_current_user)):
    try:
        rec = trade.create(
            asset=payload.asset, side=payload.side, entry=payload.entry,
            stop_loss=payload.stop_loss, targets=payload.targets,
            channel_id=int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None,
            user_id=user.get("sub") # استخدام البريد الإلكتروني للمستخدم كمعرف
        )
        return RecommendationOut.model_validate(rec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ✅ تم تغيير الحماية
@app.get(
    "/recommendations",
    response_model=list[RecommendationOut],
    dependencies=[Depends(get_current_user)],
)
def list_recs(request: Request, channel_id: int | None = None):
    items = trade.list_all(channel_id)
    return [RecommendationOut.model_validate(i) for i in items]

# ✅ تم تغيير الحماية
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

# ... (Endpoints for /report and /analytics can also be protected with Depends(get_current_user)) ...

# ✅ تسجيل راوتر المصادقة الجديد
app.include_router(auth_router.router)
#--- END OF FILE ---