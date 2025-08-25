--- START OF FILE: src/capitalguard/interfaces/webhook/tradingview.py ---
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from capitalguard.interfaces.api.deps import require_api_key
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.config import settings

router = APIRouter()

class TVSignal(BaseModel):
    symbol: str = Field(..., alias="symbol")
    side: str
    entry: float
    stop_loss: float = Field(..., alias="sl")
    targets: List[float] = []

@router.post("/webhook/tradingview")
async def tradingview_webhook(payload: TVSignal, request: Request, _=Depends(require_api_key)):
    # تحقق من سر TradingView
    tv_secret = (request.headers.get("X-TV-Secret") or "").strip()
    if (settings.TV_WEBHOOK_SECRET or "").strip() and tv_secret != (settings.TV_WEBHOOK_SECRET or "").strip():
        raise HTTPException(status_code=401, detail="Invalid TV secret")

    repo = RecommendationRepository()
    svc = TradeService(repo=repo)
    try:
        rec = svc.create(
            asset=payload.symbol,
            side=payload.side,
            entry=payload.entry,
            stop_loss=payload.stop_loss,
            targets=payload.targets,
        )
        return {"ok": True, "id": rec.id}
    except Exception as e:
        return {"ok": False, "error": str(e)}
--- END OF FILE ---