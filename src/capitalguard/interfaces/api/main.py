# src/capitalguard/interfaces/api/main.py
from fastapi import FastAPI, HTTPException, Depends, Request   # <-- أضف Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import sentry_sdk

from capitalguard.config import settings
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.interfaces.api.schemas import RecommendationIn, RecommendationOut, CloseIn, ReportOut
from capitalguard.interfaces.api.deps import limiter, require_api_key
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.interfaces.webhook.tradingview import router as tv_router

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
app.add_exception_handler(RateLimitExceeded, lambda r, e: ({"detail": "Rate limit exceeded"}, 429))
app.add_middleware(SlowAPIMiddleware)

repo = RecommendationRepository()
notifier = TelegramNotifier()
trade = TradeService(repo, notifier)
report = ReportService(repo)

@app.get("/health")
@limiter.limit("30/minute")
async def health(request: Request):           # <-- أضف request
    return {"status": "ok"}

@app.post("/recommendations", response_model=RecommendationOut, dependencies=[Depends(require_api_key)])
@limiter.limit("60/minute")
def create_rec(payload: RecommendationIn, request: Request):    # <-- أضف request
    try:
        rec = trade.create(
            asset=payload.asset, side=payload.side, entry=payload.entry,
            stop_loss=payload.stop_loss, targets=payload.targets,
            channel_id=payload.channel_id, user_id=payload.user_id
        )
        return RecommendationOut(
            id=rec.id, asset=rec.asset.value, side=rec.side.value, entry=rec.entry.value,
            stop_loss=rec.stop_loss.value, targets=rec.targets.values, status=rec.status,
            channel_id=rec.channel_id, user_id=rec.user_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/recommendations", response_model=list[RecommendationOut], dependencies=[Depends(require_api_key)])
@limiter.limit("120/minute")
def list_recs(channel_id: int | None = None, request: Request = None):  # <-- أضف request
    items = trade.list_all(channel_id)
    return [RecommendationOut(
        id=i.id, asset=i.asset.value, side=i.side.value, entry=i.entry.value,
        stop_loss=i.stop_loss.value, targets=i.targets.values, status=i.status,
        channel_id=i.channel_id, user_id=i.user_id
    ) for i in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_api_key)])
@limiter.limit("60/minute")
def close_rec(rec_id: int, payload: CloseIn, request: Request):        # <-- أضف request
    try:
        rec = trade.close(rec_id, payload.exit_price)
        return RecommendationOut(
            id=rec.id, asset=rec.asset.value, side=rec.side.value, entry=rec.entry.value,
            stop_loss=rec.stop_loss.value, targets=rec.targets.values, status=rec.status,
            channel_id=rec.channel_id, user_id=rec.user_id
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/report", response_model=ReportOut, dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
def get_report(channel_id: int | None = None, request: Request = None):  # <-- أضف request
    return report.summary(channel_id)

# Routers
app.include_router(metrics_router)
app.include_router(tv_router)