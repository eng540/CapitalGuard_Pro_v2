from fastapi import APIRouter, HTTPException, Request, Depends
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.config import settings

router = APIRouter(prefix="/webhook", tags=["webhook"])

# Simple DI
_repo = RecommendationRepository()
_notifier = TelegramNotifier()
_trade = TradeService(_repo, _notifier)

def require_secret(request: Request):
    secret = request.headers.get("X-TV-Secret") or (request.query_params.get("secret"))
    if settings.TV_WEBHOOK_SECRET and secret != settings.TV_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    return True

@router.post("/tradingview", dependencies=[Depends(require_secret)])
async def tradingview_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    required = ["asset","side","entry","stop_loss","targets"]
    if not all(k in payload for k in required):
        raise HTTPException(status_code=400, detail=f"Missing keys: {required}")

    rec = _trade.create(
        asset=str(payload["asset"]).upper().strip(),
        side=str(payload["side"]).upper().strip(),
        entry=float(payload["entry"]),
        stop_loss=float(payload["stop_loss"]),
        targets=[float(x) for x in payload["targets"]],
    )
    return {"status":"ok","id":rec.id}
