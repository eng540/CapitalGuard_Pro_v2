# --- START OF NEW FILE: src/capitalguard/interfaces/api/routers/webapp.py ---
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import hashlib
import hmac
import json
from urllib.parse import parse_qs
from decimal import Decimal

from capitalguard.config import settings
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.interfaces.telegram.parsers import parse_targets_list

router = APIRouter(prefix="/api/webapp", tags=["WebApp"])

class WebAppSignal(BaseModel):
    initData: str
    asset: str
    side: str
    market: str
    order_type: str
    entry: float
    stop_loss: float
    targets_raw: str
    notes: Optional[str] = None
    leverage: Optional[str] = "20"

def validate_telegram_data(init_data: str, bot_token: str) -> dict:
    try:
        parsed_data = parse_qs(init_data)
        if 'hash' not in parsed_data: raise ValueError("No hash")
        hash_value = parsed_data.pop('hash')[0]
        data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash != hash_value: raise ValueError("Invalid hash")
        return json.loads(parsed_data['user'][0])
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid Data")

@router.post("/create")
async def create_trade_webapp(payload: WebAppSignal, request: Request):
    user_data = validate_telegram_data(payload.initData, settings.TELEGRAM_BOT_TOKEN)
    telegram_id = user_data['id']
    
    creation_service = request.app.state.services.get("creation_service")
    if not creation_service: return {"ok": False, "error": "System initializing..."}

    try:
        with session_scope() as db_session:
            user_repo = UserRepository(db_session)
            user = user_repo.find_by_telegram_id(telegram_id)
            if not user or user.user_type.value != "ANALYST":
                return {"ok": False, "error": "Permission Denied"}

            targets_formatted = parse_targets_list(payload.targets_raw.split())
            if not targets_formatted: return {"ok": False, "error": "Invalid targets"}

            # Append leverage to notes if present
            final_notes = payload.notes or ""
            if payload.market == "FUTURES":
                final_notes = f"Lev: {payload.leverage}x | {final_notes}".strip()

            created_rec, _ = await creation_service.create_and_publish_recommendation_async(
                user_id=str(telegram_id),
                db_session=db_session,
                asset=payload.asset,
                side=payload.side,
                market=payload.market.capitalize(), # Futures/Spot
                order_type=payload.order_type,
                entry=Decimal(str(payload.entry)),
                stop_loss=Decimal(str(payload.stop_loss)),
                targets=targets_formatted,
                notes=final_notes
            )
            
            import asyncio
            asyncio.create_task(creation_service.background_publish_and_index(
                rec_id=created_rec.id, user_db_id=user.id, target_channel_ids=None
            ))

            return {"ok": True, "id": created_rec.id}
    except Exception as e:
        return {"ok": False, "error": str(e)}
# --- END OF NEW FILE ---