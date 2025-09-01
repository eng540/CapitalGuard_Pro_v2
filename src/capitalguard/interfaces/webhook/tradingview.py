#--- START OF FILE: src/capitalguard/interfaces/webhook/tradingview.py ---
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from capitalguard.application.services.trade_service import TradeService

# ✅ سنقوم بحقن الخدمة باستخدام نظام FastAPI's Dependency Injection
def get_trade_service(request: Request) -> TradeService:
    return request.app.state.services["trade_service"]

router = APIRouter(prefix="/webhook", tags=["Webhooks"])

class TVSignal(BaseModel):
    symbol: str
    side: str
    entry: float
    stop_loss: float = Field(..., alias="sl")
    targets: List[float]
    market: Optional[str] = "Futures"
    notes: Optional[str] = None

@router.post("/tradingview")
async def tradingview_webhook(
    payload: TVSignal,
    request: Request,
    trade_service: TradeService = Depends(get_trade_service)
):
    # ... (TV_WEBHOOK_SECRET check remains the same)

    try:
        rec = trade_service.create_and_publish_recommendation(
            asset=payload.symbol,
            side=payload.side,
            market=payload.market,
            entry=payload.entry,
            stop_loss=payload.stop_loss,
            targets=payload.targets,
            notes=payload.notes,
            user_id="TradingView" # Or another identifier
        )
        return {"ok": True, "id": rec.id}
    except (ValueError, RuntimeError) as e:
        # Return a meaningful error to the webhook sender
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred.")
#--- END OF FILE ---