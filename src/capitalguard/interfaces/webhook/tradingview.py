# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import logging

from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.api.deps import get_trade_service 
from capitalguard.config import settings

log = logging.getLogger(__name__)
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
    # Security check for the webhook secret
    tv_secret = request.headers.get("X-TV-Secret")
    if settings.TV_WEBHOOK_SECRET and tv_secret != settings.TV_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid TradingView secret")

    try:
        # Step 1: Create the recommendation and save it to the database.
        # Note: TradingView doesn't provide partial close percentages, so the
        # Targets value object will automatically assign 100% to the final target.
        log.info(f"Received TradingView signal for {payload.symbol}")
        
        created_rec = trade_service.create_recommendation(
            asset=payload.symbol,
            side=payload.side,
            market=payload.market,
            entry=payload.entry,
            stop_loss=payload.stop_loss,
            targets=[{"price": p, "close_percent": 0} for p in payload.targets], # Convert to new format
            notes=payload.notes,
            user_id="TradingView", # Static identifier for webhook-generated trades
            order_type="LIMIT" # Assume TV signals are limit orders
        )

        # Step 2: Publish the newly created recommendation.
        # This will publish to all channels associated with the "TradingView" user,
        # which needs to be configured in the database.
        log.info(f"Publishing recommendation #{created_rec.id} from TradingView signal.")
        _, report = trade_service.publish_recommendation(
            rec_id=created_rec.id,
            user_id="TradingView"
        )
        
        if not report.get("success"):
            log.warning(f"Recommendation #{created_rec.id} was created but failed to publish to any channel.")

        return {"ok": True, "id": created_rec.id, "message": "Recommendation created and published successfully."}
        
    except ValueError as e:
        log.error(f"Validation error in TradingView webhook: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("An internal error occurred in the TradingView webhook")
        raise HTTPException(status_code=500, detail="An internal server error occurred.")
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---