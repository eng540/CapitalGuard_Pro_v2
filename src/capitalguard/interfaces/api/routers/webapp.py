#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/api/routers/webapp.py ---
# File: src/capitalguard/interfaces/api/routers/webapp.py
# Version: v1.5.0 (Channel Visibility & Stability Fix)
# ✅ THE FIX: 
#    1. Changed list_by_analyst(..., only_active=False) to show ALL channels.
#    2. Added explicit logging for channel count to debug visibility.
# 🎯 IMPACT: Ensures channels appear in the UI even if marked inactive in DB.

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import hashlib
import hmac
import json
import logging
import asyncio
from urllib.parse import parse_qs
from decimal import Decimal

from capitalguard.config import settings
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository
from capitalguard.interfaces.telegram.parsers import parse_targets_list
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.helpers import _pct

log = logging.getLogger(__name__)
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
    channel_ids: List[int] = []

def validate_telegram_data(init_data: str, bot_token: str) -> dict:
    if not bot_token:
        raise HTTPException(status_code=500, detail="Server config error: Token missing")
    try:
        parsed_data = parse_qs(init_data)
        if 'hash' not in parsed_data:
            raise ValueError("No hash found")
        hash_value = parsed_data.pop('hash')[0]
        data_check_arr = []
        for k, v in sorted(parsed_data.items()):
            data_check_arr.append(f"{k}={v[0]}")
        data_check_string = "\n".join(data_check_arr)
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash != hash_value:
            raise ValueError("Invalid hash")
        user_json = parsed_data.get('user', ['{}'])[0]
        return json.loads(user_json)
    except Exception as e:
        log.warning(f"WebApp Auth Failed: {e}")
        raise HTTPException(status_code=403, detail="Invalid Authentication Data")

@router.get("/price")
async def get_price(symbol: str, request: Request):
    price_service = request.app.state.services.get("price_service")
    if not price_service: return {"price": 0.0}
    # Try futures first, then spot
    price = await price_service.get_cached_price(symbol.upper(), "Futures", force_refresh=False)
    if not price: 
        price = await price_service.get_cached_price(symbol.upper(), "Spot", force_refresh=False)
    return {"price": price or 0.0}

@router.get("/channels")
async def get_analyst_channels(initData: str):
    """
    Fetches ALL linked channels for the analyst.
    """
    try:
        user_data = validate_telegram_data(initData, settings.TELEGRAM_BOT_TOKEN)
        telegram_id = user_data['id']
        
        with session_scope() as db_session:
            user_repo = UserRepository(db_session)
            user = user_repo.find_by_telegram_id(telegram_id)
            
            if not user:
                 return {"ok": False, "error": "User not registered."}

            if str(user.user_type.value).upper() != "ANALYST":
                return {"ok": False, "error": "Permission Denied: Analyst role required."}
            
            channel_repo = ChannelRepository(db_session)
            
            # ✅ FIX: Get ALL channels (Active & Inactive) to match Bot behavior
            channels = channel_repo.list_by_analyst(user.id, only_active=False)
            
            log.info(f"API /channels: Found {len(channels)} channels for user {telegram_id}")
            
            return {
                "ok": True,
                "channels": [
                    {
                        "id": ch.telegram_channel_id, 
                        "title": ch.title or "Untitled",
                        "is_active": ch.is_active
                    } 
                    for ch in channels
                ]
            }
    except Exception as e:
        log.error(f"API /channels Error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

@router.post("/create")
async def create_trade_webapp(payload: WebAppSignal, request: Request):
    try:
        user_data = validate_telegram_data(payload.initData, settings.TELEGRAM_BOT_TOKEN)
        telegram_id = user_data['id']
        
        creation_service = request.app.state.services.get("creation_service")
        if not creation_service: return {"ok": False, "error": "System initializing..."}

        with session_scope() as db_session:
            user_repo = UserRepository(db_session)
            user = user_repo.find_by_telegram_id(telegram_id)
            
            if not user or str(user.user_type.value).upper() != "ANALYST":
                return {"ok": False, "error": "Permission Denied"}

            targets_formatted = parse_targets_list(payload.targets_raw.split())
            if not targets_formatted: return {"ok": False, "error": "Invalid targets format"}

            # Optional: Validate total percent (soft check)
            # total_pct = sum(t['close_percent'] for t in targets_formatted)
            
            final_notes = payload.notes or ""
            if payload.market == "FUTURES":
                final_notes = f"Lev: {payload.leverage}x | {final_notes}".strip()

            target_channels = set(payload.channel_ids) if payload.channel_ids else None

            created_rec, _ = await creation_service.create_and_publish_recommendation_async(
                user_id=str(telegram_id),
                db_session=db_session,
                asset=payload.asset,
                side=payload.side,
                market=payload.market.capitalize(),
                order_type=payload.order_type,
                entry=Decimal(str(payload.entry)),
                stop_loss=Decimal(str(payload.stop_loss)),
                targets=targets_formatted,
                notes=final_notes
            )
            
            # Background task for broadcasting
            asyncio.create_task(creation_service.background_publish_and_index(
                rec_id=created_rec.id, user_db_id=user.id, target_channel_ids=target_channels
            ))

            return {"ok": True, "id": created_rec.id}
    except Exception as e:
        log.error(f"API /create Error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

# (Note: get_user_portfolio and get_signal_details endpoints remain unchanged in logic
# but included here for file completeness if you overwrite the whole file)
@router.get("/portfolio")
async def get_user_portfolio(initData: str, request: Request):
    try:
        user_data = validate_telegram_data(initData, settings.TELEGRAM_BOT_TOKEN)
        telegram_id = user_data['id']
        trade_service = request.app.state.services.get("trade_service")
        price_service = request.app.state.services.get("price_service")

        with session_scope() as session:
            # Calling synchronous method directly (safe here as we are in a route)
            # For high load, use run_in_executor, but here direct call is okay for read-only
            # Better: use run_in_executor to avoid blocking main loop
            items = await asyncio.to_thread(trade_service.get_open_positions_for_user, session, str(telegram_id))
            
            assets_to_fetch = set((getattr(item.asset, 'value'), getattr(item, 'market', 'Futures')) for item in items)
            price_tasks = [price_service.get_cached_price(asset, market) for asset, market in assets_to_fetch]
            price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
            prices_map = {asset_market[0]: price for asset_market, price in zip(assets_to_fetch, price_results) if isinstance(price, (int, float))}

            formatted_items = []
            for item in items:
                asset_val = getattr(item.asset, 'value')
                live = prices_map.get(asset_val)
                pnl = _pct(getattr(item.entry, 'value'), live, getattr(item.side, 'value')) if live else 0.0
                formatted_items.append({
                    "id": item.id, "asset": asset_val, "side": getattr(item.side, 'value'),
                    "entry": getattr(item.entry, 'value'), "market": getattr(item, 'market', 'Futures'),
                    "is_user_trade": getattr(item, 'is_user_trade', False),
                    "unified_status": getattr(item, 'unified_status', 'WATCHLIST'),
                    "pnl_live": pnl, "live_price": live
                })
            return {"ok": True, "portfolio": {"items": formatted_items}}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@router.get("/signal/{rec_id}")
async def get_signal_details(rec_id: int, request: Request):
    try:
        trade_service = request.app.state.services.get("trade_service")
        price_service = request.app.state.services.get("price_service")
        with session_scope() as session:
            rec = trade_service.repo.get(session, rec_id)
            if not rec: return {"ok": False, "error": "Not found"}
            live = await price_service.get_cached_price(rec.asset, rec.market) or 0.0
            pnl = _pct(rec.entry, live, rec.side) if live else 0.0
            
            targets = []
            hit_set = {int(e.event_type[2:-4]) for e in rec.events if "TP" in e.event_type and "HIT" in e.event_type}
            for i, t in enumerate(rec.targets, 1):
                targets.append({"price": float(t['price']), "roi": f"{_pct(rec.entry, float(t['price']), rec.side):+.2f}", "hit": i in hit_set})
            
            events = [{"time": e.event_timestamp.strftime("%d/%m %H:%M"), "description": e.event_type} for e in rec.events]
            
            return {"ok": True, "signal": {
                "asset": rec.asset, "side": rec.side, "entry": float(rec.entry), "stop_loss": float(rec.stop_loss),
                "live_price": live, "pnl": f"{pnl:.2f}", "targets": targets, "events": events
            }}
    except Exception as e:
        return {"ok": False, "error": str(e)}
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/api/routers/webapp.py ---