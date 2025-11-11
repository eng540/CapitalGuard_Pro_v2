# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
# src/capitalguard/interfaces/webhook/tradingview.py (v2.0 - ADR-001 Async Fix)
"""
Webhook handler for TradingView alerts.
✅ THE FIX (ADR-001): This handler is now asynchronous and decoupled.
    - It no longer calls the old, non-existent `create_recommendation` function.
    - It now calls the lightweight `create_and_publish_recommendation_async`
      to save the signal instantly (as shadow=True).
    - It returns HTTP 200 to TradingView *immediately*.
    - It launches `asyncio.create_task` to run the heavy
      `background_publish_and_index` task without blocking.
    - This fixes the fatal error and makes TV signal ingestion instantaneous.
"""

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Optional
import logging
import asyncio # ✅ Added for background tasks
from decimal import Decimal

# ✅ Import services and session management
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import UserType
from capitalguard.interfaces.api.deps import get_trade_service 
from capitalguard.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhooks"])

# Define a dedicated user ID or username for TV signals
# This user MUST be created in your database and MUST have the ANALYST role.
TRADINGVIEW_ANALYST_TELEGRAM_ID = int(settings.TELEGRAM_ADMIN_CHAT_ID or 0) # Example: Re-use admin ID
if TRADINGVIEW_ANALYST_TELEGRAM_ID == 0:
    log.critical("TRADINGVIEW_ANALYST_TELEGRAM_ID is not set. Webhook will fail.")

class TVSignal(BaseModel):
    symbol: str
    side: str
    entry: Decimal # Use Decimal for precision
    stop_loss: Decimal = Field(..., alias="sl")
    targets: List[Decimal]
    market: Optional[str] = "Futures"
    notes: Optional[str] = "Signal from TradingView"
    order_type: str = "LIMIT" # Assume TV signals are limit orders


async def _run_tv_background_tasks(
    trade_service: TradeService,
    rec_id: int,
    user_db_id: int
):
    """Helper function to run the background tasks for TV webhook."""
    try:
        # We don't have target_channel_ids from TV,
        # so background_publish_and_index will publish to all
        # active channels for the TV user.
        await trade_service.background_publish_and_index(
            rec_id=rec_id,
            user_db_id=user_db_id,
            target_channel_ids=None # Publish to all linked channels
        )
    except Exception as e:
        log.error(f"[BG Task TV Rec {rec_id}]: CRITICAL FAILURE: {e}", exc_info=True)


@router.post("/tradingview")
async def tradingview_webhook(
    payload: TVSignal,
    request: Request,
    background_tasks: BackgroundTasks, # Use FastAPI's background tasks
    trade_service: TradeService = Depends(get_trade_service)
):
    # Security check for the webhook secret
    tv_secret = request.headers.get("X-TV-Secret")
    if settings.TV_WEBHOOK_SECRET and tv_secret != settings.TV_WEBHOOK_SECRET:
        log.warning("Invalid TradingView secret received.")
        raise HTTPException(status_code=401, detail="Invalid TradingView secret")

    log.info(f"Received TradingView signal for {payload.symbol}")

    try:
        # Get a new DB session for this request
        with session_scope() as db_session:
            # 1. Find the dedicated TradingView analyst user
            # We use a known ID here for performance.
            user_repo = UserRepository(db_session)
            tv_user = user_repo.find_by_telegram_id(TRADINGVIEW_ANALYST_TELEGRAM_ID)
            
            if not tv_user or tv_user.user_type != UserType.ANALYST:
                log.error(f"TradingView user (ID: {TRADINGVIEW_ANALYST_TELEGRAM_ID}) not found or is not an Analyst.")
                raise HTTPException(status_code=500, detail="TradingView user not configured correctly.")

            user_db_id = tv_user.id
            user_telegram_id_str = str(tv_user.telegram_user_id)

            # 2. Convert Pydantic model to dictionary for the service
            rec_data = {
                "asset": payload.symbol,
                "side": payload.side.upper(),
                "market": payload.market,
                "entry": payload.entry,
                "stop_loss": payload.stop_loss,
                # Convert targets to the format TradeService expects
                "targets": [{"price": p, "close_percent": 0.0} for p in payload.targets],
                "notes": payload.notes,
                "order_type": payload.order_type.upper(),
                "exit_strategy": "CLOSE_AT_FINAL_TP" # Default strategy
            }
            # Manually set 100% on last target if none provided
            if rec_data["targets"] and all(t["close_percent"] == 0.0 for t in rec_data["targets"]):
                rec_data["targets"][-1]["close_percent"] = 100.0

            # 3. Call the *lightweight* save function (ADR-001)
            # This saves as is_shadow=True and returns instantly
            created_rec, _ = await trade_service.create_and_publish_recommendation_async(
                user_id=user_telegram_id_str,
                db_session=db_session,
                **rec_data
            )
            
            rec_id = created_rec.id
            log.info(f"TradingView signal saved as shadow Rec ID #{rec_id}.")

        # 4. Add the *heavy* work to background tasks
        # This will run *after* the HTTP 200 response is sent
        background_tasks.add_task(
            _run_tv_background_tasks,
            trade_service=trade_service,
            rec_id=rec_id,
            user_db_id=user_db_id
        )
        
        # 5. Return HTTP 200 OK *immediately* to TradingView
        return {"ok": True, "id": rec_id, "message": "Recommendation accepted and processing in background."}
        
    except ValueError as e:
        log.error(f"Validation error in TradingView webhook: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("An internal error occurred in the TradingView webhook")
        raise HTTPException(status_code=500, detail="An internal server error occurred.")
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---