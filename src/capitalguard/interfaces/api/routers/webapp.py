# --- START OF CONSOLIDATED & IMPROVED FILE: src/capitalguard/interfaces/api/routers/webapp.py ---
# File: src/capitalguard/interfaces/api/routers/webapp.py
# Version: v2.3.0-CONSOLIDATED (All fixes + Enhanced Error Handling)
# ✅ INCORPORATES ALL FIXES:
#    1. Fixed datetime comparison between naive and aware datetimes
#    2. Fixed NameError by correctly retrieving services from app state
#    3. Fixed Channel Visibility (only_active=False)
#    4. Enhanced error handling and logging
#    5. Improved data validation and parsing

import logging
import json
import hmac
import hashlib
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Dict, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, validator

from capitalguard.config import settings
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository
from capitalguard.interfaces.telegram.parsers import parse_targets_list
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.interfaces.telegram.helpers import _pct, _to_decimal

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webapp", tags=["WebApp"])

# --- Models ---
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

    @validator('asset')
    def asset_uppercase(cls, v):
        return v.upper()

    @validator('side')
    def side_uppercase(cls, v):
        return v.upper()

class TradeAction(BaseModel):
    initData: str
    action: str  # close, breakeven, edit, partial
    trade_id: int
    value: Optional[str] = None

# --- Helpers ---
def validate_telegram_data(init_data: str, bot_token: str) -> dict:
    """Validate Telegram WebApp authentication data"""
    if not bot_token:
        log.error("Bot token not configured")
        raise HTTPException(status_code=500, detail="Server configuration error")
    
    try:
        parsed_data = parse_qs(init_data)
        if 'hash' not in parsed_data:
            raise ValueError("No hash found in initData")
        
        hash_value = parsed_data.pop('hash')[0]
        data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed_data.items()))
        
        # Generate secret key and calculate hash
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(calc_hash, hash_value):
            raise ValueError("Invalid authentication hash")
        
        user_json = parsed_data.get('user', ['{}'])[0]
        return json.loads(user_json)
        
    except json.JSONDecodeError as e:
        log.warning(f"JSON decode error in Telegram auth: {e}")
        raise HTTPException(status_code=403, detail="Invalid user data")
    except Exception as e:
        log.warning(f"Telegram auth error: {e}")
        raise HTTPException(status_code=403, detail="Authentication failed")

def _ensure_timezone_aware(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware (UTC)"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def _format_time_ago(created_dt: datetime) -> str:
    """Format time difference in human-readable format"""
    now = datetime.now(timezone.utc)
    created = _ensure_timezone_aware(created_dt)
    time_diff = now - created
    
    if time_diff.days > 0:
        hours = time_diff.seconds // 3600
        return f"{time_diff.days}d {hours}h ago"
    elif time_diff.seconds >= 3600:
        hours = time_diff.seconds // 3600
        minutes = (time_diff.seconds % 3600) // 60
        return f"{hours}h {minutes}m ago"
    else:
        minutes = time_diff.seconds // 60
        return f"{minutes}m ago"

def _extract_leverage_from_notes(notes: str) -> str:
    """Extract leverage information from trade notes"""
    if not notes or "Lev:" not in notes:
        return "20x"
    
    try:
        leverage_part = notes.split("Lev:")[1].split("|")[0].strip()
        return leverage_part
    except Exception:
        return "20x"

# --- Endpoints ---

@router.get("/price")
async def get_price(symbol: str, request: Request):
    """Get live price for symbol (Futures first, then Spot fallback)"""
    try:
        price_svc = request.app.state.services.get("price_service")
        if not price_svc:
            log.warning("Price service unavailable")
            return {"price": 0.0}
        
        symbol_upper = symbol.upper()
        
        # Try Futures first, then Spot as fallback
        price = await price_svc.get_cached_price(symbol_upper, "Futures", False)
        if not price:
            price = await price_svc.get_cached_price(symbol_upper, "Spot", False)
            
        return {"price": price or 0.0}
    except Exception as e:
        log.error(f"Price fetch error for {symbol}: {e}")
        return {"price": 0.0}

@router.get("/channels")
async def get_analyst_channels(initData: str):
    """Get all channels for analyst (both active and inactive)"""
    try:
        user_data = validate_telegram_data(initData, settings.TELEGRAM_BOT_TOKEN)
        
        with session_scope() as session:
            repo = UserRepository(session)
            user = repo.find_by_telegram_id(user_data['id'])
            
            if not user or str(user.user_type.value).upper() != "ANALYST":
                return {"ok": False, "error": "Analyst role required"}
            
            # Fetch all channels (active and inactive)
            channels = ChannelRepository(session).list_by_analyst(user.id, only_active=False)
            
            return {
                "ok": True,
                "channels": [
                    {
                        "id": ch.telegram_channel_id, 
                        "title": ch.title or "Untitled Channel", 
                        "is_active": ch.is_active
                    } 
                    for ch in channels
                ]
            }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Channels fetch error: {e}")
        return {"ok": False, "error": "Failed to fetch channels"}

@router.post("/create")
async def create_trade_webapp(payload: WebAppSignal, request: Request):
    """Create and publish new trading signal"""
    try:
        user_data = validate_telegram_data(payload.initData, settings.TELEGRAM_BOT_TOKEN)
        creation_service = request.app.state.services.get("creation_service")
        
        if not creation_service:
            return {"ok": False, "error": "Creation service unavailable"}
        
        with session_scope() as session:
            user_repo = UserRepository(session)
            user = user_repo.find_by_telegram_id(user_data['id'])
            
            if not user or str(user.user_type.value).upper() != "ANALYST":
                return {"ok": False, "error": "Analyst permission required"}
            
            # Parse targets
            targets = parse_targets_list(payload.targets_raw.split())
            if not targets:
                return {"ok": False, "error": "Invalid targets format"}
            
            # Prepare notes with leverage
            notes = f"Lev: {payload.leverage}x"
            if payload.notes and payload.notes.strip():
                notes += f" | {payload.notes.strip()}"
            
            # Create recommendation
            rec, _ = await creation_service.create_and_publish_recommendation_async(
                user_id=str(user_data['id']),
                db_session=session,
                asset=payload.asset,
                side=payload.side,
                market=payload.market,
                order_type=payload.order_type,
                entry=Decimal(str(payload.entry)),
                stop_loss=Decimal(str(payload.stop_loss)),
                targets=targets,
                notes=notes
            )
            
            # Background publishing
            target_channels = set(payload.channel_ids) if payload.channel_ids else None
            asyncio.create_task(
                creation_service.background_publish_and_index(
                    rec_id=rec.id,
                    user_db_id=user.id,
                    target_channel_ids=target_channels
                )
            )
            
            return {"ok": True, "id": rec.id}
            
    except Exception as e:
        log.error(f"Trade creation error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

@router.get("/portfolio")
async def get_user_portfolio(initData: str, request: Request):
    """Get user portfolio with live prices and calculated PnL"""
    try:
        user_data = validate_telegram_data(initData, settings.TELEGRAM_BOT_TOKEN)
        telegram_id = user_data['id']
        
        # Get services
        trade_service = request.app.state.services.get("trade_service")
        price_service = request.app.state.services.get("price_service")
        
        if not trade_service or not price_service:
            log.error("Required services not available in portfolio endpoint")
            return {"ok": False, "error": "System services unavailable"}

        with session_scope() as session:
            # Get open positions
            items = trade_service.get_open_positions_for_user(session, str(telegram_id))
            
            # Fetch prices in parallel
            assets_to_fetch = set(
                (getattr(item.asset, 'value'), getattr(item, 'market', 'Futures')) 
                for item in items
            )
            
            price_tasks = [
                price_service.get_cached_price(asset, market) 
                for asset, market in assets_to_fetch
            ]
            
            price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
            prices_map = {
                asset_market[0]: price 
                for asset_market, price in zip(assets_to_fetch, price_results) 
                if isinstance(price, (int, float)) and price > 0
            }

            # Format portfolio items
            formatted_items = []
            for item in items:
                asset_val = getattr(item.asset, 'value')
                side_val = getattr(item.side, 'value')
                live_price = prices_map.get(asset_val)
                entry_val = _to_decimal(getattr(item.entry, 'value'))
                
                # Calculate PnL
                pnl = _pct(entry_val, live_price, side_val) if live_price else 0.0
                
                # Extract targets
                targets_ui = []
                raw_targets = getattr(item.targets, 'values', [])
                for target in raw_targets:
                    target_price = _to_decimal(getattr(target, 'price'))
                    is_hit = (
                        (side_val == "LONG" and live_price and live_price >= target_price) or
                        (side_val == "SHORT" and live_price and live_price <= target_price)
                    )
                    targets_ui.append({
                        "price": float(target_price),
                        "percent": getattr(target, 'close_percent', 0),
                        "hit": bool(is_hit)
                    })
                
                # Format time ago
                created_dt = getattr(item, 'created_at', datetime.now(timezone.utc))
                time_ago = _format_time_ago(created_dt)
                
                # Extract leverage
                notes = getattr(item, 'notes', '') or ''
                leverage = _extract_leverage_from_notes(notes)
                
                # Build item data
                formatted_items.append({
                    "id": item.id,
                    "asset": asset_val,
                    "side": side_val,
                    "market": getattr(item, 'market', 'Futures'),
                    "entry": float(entry_val),
                    "stop_loss": float(_to_decimal(getattr(item.stop_loss, 'value'))),
                    "live_price": live_price,
                    "pnl_live": pnl,
                    "unified_status": getattr(item, 'unified_status', 'WATCHLIST'),
                    "is_user_trade": getattr(item, 'is_user_trade', False),
                    "leverage": leverage,
                    "time_ago": time_ago,
                    "targets": targets_ui
                })

            return {"ok": True, "portfolio": {"items": formatted_items}}

    except Exception as e:
        log.error(f"Portfolio fetch error: {e}", exc_info=True)
        return {"ok": False, "error": "Failed to load portfolio"}

@router.post("/action")
async def handle_trade_action(payload: TradeAction, request: Request):
    """Handle trade actions (close, breakeven, partial close)"""
    try:
        user_data = validate_telegram_data(payload.initData, settings.TELEGRAM_BOT_TOKEN)
        lifecycle_service = request.app.state.services.get("lifecycle_service")
        
        if not lifecycle_service:
            return {"ok": False, "error": "Lifecycle service unavailable"}
        
        with session_scope() as session:
            user_id = str(user_data['id'])
            
            if payload.action == "close":
                price_service = request.app.state.services.get("price_service")
                if not price_service:
                    return {"ok": False, "error": "Price service unavailable"}
                
                rec = lifecycle_service.repo.get(session, payload.trade_id)
                if not rec:
                    return {"ok": False, "error": "Trade not found"}
                
                live_price = await price_service.get_cached_price(rec.asset, rec.market, True)
                await lifecycle_service.close_recommendation_async(
                    payload.trade_id, user_id, Decimal(str(live_price)), session, "WEB_CLOSE"
                )
                return {"ok": True, "message": f"Closed at {live_price}"}
            
            elif payload.action == "breakeven":
                await lifecycle_service.move_sl_to_breakeven_async(payload.trade_id, session)
                return {"ok": True, "message": "Stop loss moved to breakeven"}
            
            elif payload.action == "partial":
                price_service = request.app.state.services.get("price_service")
                if not price_service:
                    return {"ok": False, "error": "Price service unavailable"}
                
                rec = lifecycle_service.repo.get(session, payload.trade_id)
                if not rec:
                    return {"ok": False, "error": "Trade not found"}
                
                live_price = await price_service.get_cached_price(rec.asset, rec.market, True)
                close_percent = Decimal(payload.value) if payload.value else Decimal("50")
                
                await lifecycle_service.partial_close_async(
                    payload.trade_id, user_id, close_percent, Decimal(str(live_price)), session, "WEB_PARTIAL"
                )
                return {"ok": True, "message": f"Closed {close_percent}%"}
            
            else:
                return {"ok": False, "error": f"Unknown action: {payload.action}"}
                
    except Exception as e:
        log.error(f"Trade action error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"ok": True, "status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

# --- END OF CONSOLIDATED & IMPROVED FILE ---