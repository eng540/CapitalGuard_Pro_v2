#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/api/routers/webapp.py ---
# File: src/capitalguard/interfaces/api/routers/webapp.py
# Version: v2.4.0-ANALYTICS-FIX
# ✅ THE FIX: Restored and implemented 'get_signal_details' endpoint.
# 🎯 IMPACT: Fixes the "Open Analytics" button error in Telegram.

import logging
import json
import hmac
import hashlib
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Header, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from capitalguard.config import get_r5_observation_status, settings
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository, RecommendationRepository
from capitalguard.interfaces.telegram.parsers import parse_targets_list
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.application.services.performance_service import PerformanceService
from capitalguard.application.services.analyst_discovery_service import AnalystDiscoveryService
from capitalguard.application.services.historical_reputation_service import HistoricalReputationService
from capitalguard.application.services.historical_signal_query_service import HistoricalSignalQueryService
from capitalguard.application.services.historical_web_intake_service import HistoricalWebIntakeError, HistoricalWebIntakeService
from capitalguard.application.services.historical_trust_release_service import HistoricalTrustReleaseService
from capitalguard.application.services.web_command_service import WebCommandError, WebCommandService
from capitalguard.interfaces.telegram.helpers import _pct, _to_decimal
from capitalguard.infrastructure.db.models import Channel, HistoricalImportBatch, HistoricalSignalEvent, PublicationDelivery, Recommendation, RecommendationEvent, RecommendationStatusEnum, UserTrade, WebCommandAudit
from capitalguard.infrastructure.market.symbol_catalog import SymbolCatalog

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webapp", tags=["WebApp"])
TRADER_RECOMMENDATION_SCHEMA_VERSION = "2026-08-21.2"
ANALYST_ASSET_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")

# --- Models ---
class WebAppSignal(BaseModel):
    initData: Optional[str] = None
    actor_telegram_id: Optional[int] = None
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


class AnalystRecommendationConfirm(WebAppSignal):
    idempotency_key: str


class LegacyWebAppSignal(WebAppSignal):
    idempotency_key: Optional[str] = None


class UserTradeCloseCommand(BaseModel):
    actor_telegram_id: int
    idempotency_key: str


class UserTradePartialCloseCommand(UserTradeCloseCommand):
    close_percent: Decimal


class UserTradeEntryUpdateCommand(UserTradeCloseCommand):
    entry: Decimal


class UserTradeCancelCommand(BaseModel):
    actor_telegram_id: int
    idempotency_key: str


class TelegramSessionVerification(BaseModel):
    init_data: str


class OwnerReviewCommand(BaseModel):
    actor_telegram_id: int
    batch_id: int
    approved: bool
    note: Optional[str] = None
    idempotency_key: str


class EvidenceIngestCommand(BaseModel):
    actor_telegram_id: int
    batch_id: int
    idempotency_key: str


class HistoricalBinanceReplayCommand(BaseModel):
    actor_telegram_id: int
    signal_id: int
    start: datetime
    end: datetime
    interval: str = "1m"
    limit: int = 1500
    idempotency_key: str


class HistoricalBatchBinanceReplayCommand(BaseModel):
    actor_telegram_id: int
    batch_id: int
    idempotency_key: str


class HistoricalG6ReplayCommand(BaseModel):
    actor_telegram_id: int
    signal_id: int
    idempotency_key: str


class HistoricalDraftMaterializationCommand(BaseModel):
    actor_telegram_id: int
    draft_id: int
    idempotency_key: str


class HistoricalWebIntakeItem(BaseModel):
    item_key: Optional[str] = None
    raw_text: Optional[str] = None
    source_chat_id: Optional[int] = None
    source_message_id: Optional[int] = None
    source_message_revision: int = 0
    source_message_timestamp: Optional[datetime] = None
    source_reply_to_message_id: Optional[int] = None
    source_uri: Optional[str] = None
    source_origin_type: Optional[str] = None
    related_item_key: Optional[str] = None
    media: Optional[Dict[str, Any]] = None


class HistoricalWebIntakeCommand(BaseModel):
    actor_telegram_id: int
    source_kind: str = "MANUAL_ADMIN_IMPORT"
    input_mode: str = "PASTE"
    items: List[HistoricalWebIntakeItem]
    is_partial: bool = False
    batch_label: Optional[str] = None

# --- Helpers ---
def validate_telegram_data(init_data: str, bot_token: str) -> dict:
    if not bot_token:
        raise HTTPException(status_code=500, detail="Server Config Error")
    try:
        parsed_data = parse_qs(init_data)
        if 'hash' not in parsed_data: raise ValueError("No hash found")
        hash_value = parsed_data.pop('hash')[0]
        data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calc_hash != hash_value: raise ValueError("Invalid hash")
        return json.loads(parsed_data['user'][0])
    except Exception as e:
        log.warning(f"Auth Error: {e}")
        raise HTTPException(status_code=403, detail="Authentication Failed")


def require_core_service_key(authorization: str | None = Header(default=None)) -> None:
    """Authorize server-to-server Web reads; never expose this key to a browser."""
    configured_key = settings.API_KEY
    if not configured_key:
        raise HTTPException(status_code=503, detail="Core service API key is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Core service authorization required")
    supplied_key = authorization.removeprefix("Bearer ").strip()
    if not supplied_key or not hmac.compare_digest(supplied_key, configured_key):
        raise HTTPException(status_code=403, detail="Core service authorization rejected")


def resolve_webapp_actor(payload: WebAppSignal, request: Request) -> int:
    """Resolve a Mini App actor or a server-authenticated Web actor inside Core."""
    init_data = (payload.initData or "").strip()
    if init_data:
        return int(validate_telegram_data(init_data, settings.TELEGRAM_BOT_TOKEN)["id"])
    require_core_service_key(request.headers.get("authorization"))
    if not isinstance(payload.actor_telegram_id, int) or payload.actor_telegram_id <= 0:
        raise HTTPException(status_code=422, detail="Authenticated Web actor is required")
    return payload.actor_telegram_id


@router.post("/telegram/verify")
async def verify_telegram_session(payload: TelegramSessionVerification, request: Request):
    """Verify Mini App initData for the Web server without putting it in a URL."""
    require_core_service_key(request.headers.get("authorization"))
    init_data = payload.init_data.strip()
    if not init_data or len(init_data) > 10_000:
        raise HTTPException(status_code=422, detail="Telegram initData is required")
    user_data = validate_telegram_data(init_data, settings.TELEGRAM_BOT_TOKEN)
    telegram_id = user_data.get("id")
    if not isinstance(telegram_id, int) or telegram_id <= 0:
        raise HTTPException(status_code=403, detail="Telegram identity is invalid")
    return {"ok": True, "telegram_id": telegram_id}


def _serialize_live_position(entity: Any, live_price: float | None) -> dict[str, Any]:
    asset = getattr(getattr(entity, "asset", None), "value", getattr(entity, "asset", ""))
    side = getattr(getattr(entity, "side", None), "value", getattr(entity, "side", ""))
    entry = _to_decimal(getattr(getattr(entity, "entry", None), "value", 0))
    stop_loss = _to_decimal(getattr(getattr(entity, "stop_loss", None), "value", 0))
    targets = []
    for target in getattr(getattr(entity, "targets", None), "values", []) or []:
        target_price = _to_decimal(getattr(target, "price", 0))
        targets.append({
            "price": float(target_price),
            "percent": getattr(target, "close_percent", 0),
            "hit": (side == "LONG" and live_price is not None and live_price >= float(target_price))
            or (side == "SHORT" and live_price is not None and live_price <= float(target_price)),
        })
    created_at = getattr(entity, "created_at", None)
    return {
        "id": int(getattr(entity, "id")),
        "asset": str(asset),
        "side": str(side),
        "market": str(getattr(entity, "market", "Futures")),
        "entry": float(entry),
        "stop_loss": float(stop_loss),
        "live_price": live_price,
        "pnl_live_pct": _pct(entry, live_price, side) if live_price is not None else 0.0,
        "status": str(getattr(entity, "unified_status", "WATCHLIST")),
        "source_type": "TRADER_LOG" if getattr(entity, "is_user_trade", False) else "ANALYST_RECOMMENDATION",
        "targets": targets,
        "created_at": created_at.isoformat() if created_at else None,
    }


def _serialize_trade_read_model(trade: UserTrade) -> dict[str, Any]:
    events = sorted(getattr(trade, "events", []) or [], key=lambda item: (item.event_timestamp, item.id), reverse=True)[:8]
    source = getattr(trade, "source_recommendation", None)
    public_ref = trade.public_ref or f"TR-{trade.id}"
    return {
        # `id` remains only as a temporary compatibility field for the existing
        # read-only Web client. New clients must use public_ref/display_ref.
        "id": int(trade.id),
        "entity_type": "USER_TRADE",
        "public_ref": public_ref,
        "display_ref": public_ref,
        "asset": trade.asset,
        "side": trade.side,
        "market": "Futures",
        "entry": float(trade.entry),
        "stop_loss": float(trade.stop_loss),
        "open_size_percent": float(trade.open_size_percent),
        "targets": trade.targets or [],
        "status": getattr(trade.status, "value", str(trade.status)),
        "source_type": trade.source_type,
        "source": {
            "entity_type": "RECOMMENDATION",
            "public_ref": getattr(source, "public_ref", None),
            "analyst_id": getattr(source, "analyst_id", None),
        } if source else None,
        "created_at": trade.created_at.isoformat() if trade.created_at else None,
        "activated_at": trade.activated_at.isoformat() if trade.activated_at else None,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
        "timeline": [
            {"event_type": event.event_type, "event_timestamp": event.event_timestamp.isoformat()}
            for event in events
        ],
    }


def _find_owned_user_trade_by_public_ref(session: Any, user_id: int, public_ref: str) -> UserTrade | None:
    """Resolve a trader-owned read model without exposing numeric-ID lookups."""
    return session.execute(
        select(UserTrade).where(
            UserTrade.user_id == user_id,
            UserTrade.public_ref == public_ref,
        )
    ).scalar_one_or_none()


def _serialize_historical_signal_read_model(signal: Any) -> dict[str, Any]:
    return {
        "public_ref": signal.public_ref,
        "asset": signal.asset,
        "side": signal.side,
        "status": signal.status,
        "trust_tier": signal.trust_tier,
        "eligible_for_ranking": bool(signal.eligible_for_ranking),
        "decision_timestamp": signal.decision_timestamp.isoformat() if signal.decision_timestamp else None,
    }

# --- Endpoints ---

@router.get("/price")
async def get_price(symbol: str, request: Request):
    price_svc = request.app.state.services.get("price_service")
    if not price_svc: return {"price": 0.0}
    price = await price_svc.get_cached_price(symbol.upper(), "Futures", False)
    if not price: price = await price_svc.get_cached_price(symbol.upper(), "Spot", False)
    return {"price": price or 0.0}

@router.get("/channels")
async def get_analyst_channels(initData: str):
    try:
        user_data = validate_telegram_data(initData, settings.TELEGRAM_BOT_TOKEN)
        with session_scope() as session:
            repo = UserRepository(session)
            user = repo.find_by_telegram_id(user_data['id'])
            if not user or str(user.user_type.value).upper() != "ANALYST":
                return {"ok": False, "error": "Analyst role required"}
            channels = ChannelRepository(session).list_by_analyst(user.id, only_active=False)
            return {"ok": True, "channels": [{"id": ch.telegram_channel_id, "title": ch.title, "is_active": ch.is_active} for ch in channels]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/recommendations/channels")
async def get_analyst_recommendation_channels(actor_telegram_id: int, request: Request):
    """Return only the authenticated analyst's active publication channels for Web selection."""
    require_core_service_key(request.headers.get("authorization"))
    if actor_telegram_id <= 0:
        raise HTTPException(status_code=422, detail="Authenticated Web actor is required")
    with session_scope() as session:
        analyst = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if not analyst or str(getattr(analyst.user_type, "value", analyst.user_type)).upper() != "ANALYST":
            raise HTTPException(status_code=403, detail="Analyst role required")
        channels = ChannelRepository(session).list_by_analyst(analyst.id, only_active=True)
        return {
            "ok": True,
            "items": [
                {"id": channel.telegram_channel_id, "title": channel.title or str(channel.telegram_channel_id), "username": channel.username}
                for channel in channels
            ],
        }


@router.get("/recommendations/assets")
async def get_analyst_recommendation_assets(market: str, request: Request):
    """Return Core-owned explicit assets for the selected display market only."""
    require_core_service_key(request.headers.get("authorization"))
    normalized_market = market.strip().upper()
    if normalized_market not in {"SPOT", "FUTURES"}:
        raise HTTPException(status_code=422, detail="Market must be Spot or Futures")
    catalog = SymbolCatalog.binance(
        list(ANALYST_ASSET_SYMBOLS),
        market="spot" if normalized_market == "SPOT" else "Futures-USD-M",
    )
    return {
        "ok": True,
        "market": "Spot" if normalized_market == "SPOT" else "Futures",
        "items": [
            {"symbol": entry.canonical, "venue": entry.venue.value, "provider_symbol": entry.provider_symbol, "market": entry.market}
            for entry in catalog.entries()
        ],
    }


@router.get("/recommendations/{public_ref:path}/publication")
async def get_analyst_recommendation_publication(public_ref: str, actor_telegram_id: int, request: Request):
    """Return Core-owned, analyst-scoped Outbox delivery state without promising Telegram delivery early."""
    require_core_service_key(request.headers.get("authorization"))
    if actor_telegram_id <= 0:
        raise HTTPException(status_code=422, detail="Authenticated Web actor is required")
    with session_scope() as session:
        analyst = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if not analyst or str(getattr(analyst.user_type, "value", analyst.user_type)).upper() != "ANALYST":
            raise HTTPException(status_code=403, detail="Analyst role required")
        recommendation = session.query(Recommendation).filter(
            Recommendation.public_ref == public_ref.strip(),
            Recommendation.analyst_id == analyst.id,
        ).first()
        if recommendation is None:
            raise HTTPException(status_code=404, detail="Recommendation was not found")
        deliveries = session.execute(
            select(PublicationDelivery)
            .where(
                PublicationDelivery.recommendation_id == recommendation.id,
                PublicationDelivery.operation == "CREATE",
            )
            .order_by(PublicationDelivery.telegram_channel_id.asc())
        ).scalars().all()
        channel_titles = {
            channel.telegram_channel_id: channel.title or channel.username or str(channel.telegram_channel_id)
            for channel in session.execute(
                select(Channel).where(Channel.analyst_id == analyst.id)
            ).scalars().all()
        }
        state_map = {
            "PENDING": "QUEUED",
            "PROCESSING": "PUBLISHING",
            "SENT": "DELIVERED",
            "RETRY": "RETRYING",
            "FAILED": "FAILED",
        }
        items = [
            {
                "channel_id": delivery.telegram_channel_id,
                "channel_title": channel_titles.get(delivery.telegram_channel_id, str(delivery.telegram_channel_id)),
                "state": state_map.get(delivery.status, "QUEUED"),
                "attempts": delivery.attempts,
                "next_attempt_at": delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None,
                "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
                "failure_code": "DELIVERY_FAILED" if delivery.status == "FAILED" else "RETRY_SCHEDULED" if delivery.status == "RETRY" else None,
            }
            for delivery in deliveries
        ]
        states = {item["state"] for item in items}
        if not items:
            state = "SAVED"
        elif states == {"DELIVERED"}:
            state = "DELIVERED"
        elif "FAILED" in states:
            state = "FAILED"
        elif "RETRYING" in states:
            state = "RETRYING"
        elif "PUBLISHING" in states:
            state = "PUBLISHING"
        else:
            state = "QUEUED"
        return {
            "ok": True,
            "schema_version": "2026-08-22.1",
            "public_ref": recommendation.public_ref,
            "publication": {
                "state": state,
                "delivery_count": len(items),
                "delivered_count": sum(item["state"] == "DELIVERED" for item in items),
                "retrying_count": sum(item["state"] == "RETRYING" for item in items),
                "failed_count": sum(item["state"] == "FAILED" for item in items),
                "channels": items,
            },
        }

@router.post("/recommendations/preview")
async def preview_recommendation_webapp(payload: WebAppSignal, request: Request):
    """Validate an analyst recommendation without persisting any financial record."""
    try:
        actor_telegram_id = resolve_webapp_actor(payload, request)
        service = request.app.state.services.get("creation_service") if request.app.state.services else None
        if not service:
            return {"ok": False, "error": {"code": "SERVICE_UNAVAILABLE", "message": "Recommendation service unavailable"}}
        targets = parse_targets_list(payload.targets_raw.split())
        if not targets:
            return {"ok": False, "error": {"code": "INVALID_TARGETS", "message": "At least one valid target is required"}}
        with session_scope() as session:
            preview = await service.preview_recommendation_async(
                user_id=str(actor_telegram_id),
                db_session=session,
                asset=payload.asset,
                side=payload.side,
                market=payload.market,
                order_type=payload.order_type,
                entry=Decimal(str(payload.entry)),
                stop_loss=Decimal(str(payload.stop_loss)),
                targets=targets,
                notes=f"Lev: {payload.leverage}x | {payload.notes or ''}".strip(),
                target_channel_ids={int(channel_id) for channel_id in (payload.channel_ids or [])},
            )
        return {"ok": True, "preview": preview}
    except HTTPException:
        raise
    except (KeyError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": {"code": "VALIDATION_ERROR", "message": str(exc)}}
    except Exception:
        log.exception("Analyst recommendation preview failed")
        return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "Unable to preview recommendation"}}


@router.post("/recommendations/confirm")
async def confirm_recommendation_webapp(payload: AnalystRecommendationConfirm, request: Request):
    """Persist one already-reviewed recommendation through the idempotent Core command boundary."""
    key = payload.idempotency_key.strip()
    if len(key) < 16 or len(key) > 128:
        return {"ok": False, "error": {"code": "INVALID_IDEMPOTENCY_KEY", "message": "Idempotency key must contain 16 to 128 characters"}}
    try:
        actor_telegram_id = resolve_webapp_actor(payload, request)
        creation_service = request.app.state.services.get("creation_service") if request.app.state.services else None
        if not creation_service:
            return {"ok": False, "error": {"code": "SERVICE_UNAVAILABLE", "message": "Recommendation service unavailable"}}
        targets = parse_targets_list(payload.targets_raw.split())
        if not targets:
            return {"ok": False, "error": {"code": "INVALID_TARGETS", "message": "At least one valid target is required"}}
        recommendation = {
            "asset": payload.asset,
            "side": payload.side,
            "market": payload.market,
            "order_type": payload.order_type,
            "entry": Decimal(str(payload.entry)),
            "stop_loss": Decimal(str(payload.stop_loss)),
            "targets": targets,
            "notes": f"Lev: {payload.leverage}x | {payload.notes or ''}".strip(),
            "target_channel_ids": {int(channel_id) for channel_id in (payload.channel_ids or [])},
        }
        with session_scope() as session:
            return await WebCommandService().confirm_analyst_recommendation(
                session,
                actor_telegram_id=actor_telegram_id,
                idempotency_key=key,
                creation_service=creation_service,
                recommendation=recommendation,
            )
    except HTTPException:
        raise
    except WebCommandError as exc:
        return {"ok": False, "error": {"code": "COMMAND_REJECTED", "message": str(exc)}}
    except (KeyError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": {"code": "VALIDATION_ERROR", "message": str(exc)}}
    except Exception:
        log.exception("Analyst recommendation confirmation failed")
        return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "Unable to confirm recommendation"}}


def _web_recommendation_from_payload(payload: WebAppSignal) -> dict:
    """Convert either Web create payload into the one canonical command payload."""
    targets = parse_targets_list(payload.targets_raw.split())
    if not targets:
        raise ValueError("At least one valid target is required")
    return {
        "asset": payload.asset,
        "side": payload.side,
        "market": payload.market,
        "order_type": payload.order_type,
        "entry": Decimal(str(payload.entry)),
        "stop_loss": Decimal(str(payload.stop_loss)),
        "targets": targets,
        "notes": f"Lev: {payload.leverage}x | {payload.notes or ''}".strip(),
        "target_channel_ids": {
            int(channel_id) for channel_id in (payload.channel_ids or [])
        },
    }


@router.post("/create", deprecated=True)
async def create_trade_webapp(
    payload: LegacyWebAppSignal,
    request: Request,
    response: Response,
):
    """Deprecated compatibility adapter; it owns no creation logic."""
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        '</api/webapp/recommendations/confirm; rel="successor-version"'
    )
    try:
        actor_telegram_id = resolve_webapp_actor(payload, request)
        creation_service = (
            request.app.state.services.get("creation_service")
            if request.app.state.services
            else None
        )
        if not creation_service:
            return {
                "ok": False,
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": "Recommendation service unavailable",
                },
            }
        recommendation = _web_recommendation_from_payload(payload)
        idempotency_key = payload.idempotency_key or (
            WebCommandService.derive_compatibility_idempotency_key(
                actor_telegram_id,
                recommendation,
            )
        )
        with session_scope() as session:
            result = await WebCommandService().confirm_analyst_recommendation(
                session,
                actor_telegram_id=actor_telegram_id,
                idempotency_key=idempotency_key,
                creation_service=creation_service,
                recommendation=recommendation,
            )
        return result
    except HTTPException:
        raise
    except WebCommandError as exc:
        return {"ok": False, "error": {"code": "COMMAND_REJECTED", "message": str(exc)}}
    except (KeyError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": {"code": "VALIDATION_ERROR", "message": str(exc)}}
    except Exception:
        log.exception("Deprecated Web recommendation create adapter failed")
        return {
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Unable to create recommendation",
            },
        }

@router.get("/portfolio")
async def get_user_portfolio(initData: str, request: Request):
    try:
        user_data = validate_telegram_data(initData, settings.TELEGRAM_BOT_TOKEN)
        telegram_id = user_data['id']
        trade_service = request.app.state.services.get("trade_service")
        price_service = request.app.state.services.get("price_service")
        
        if not trade_service or not price_service:
             return {"ok": False, "error": "System unavailable"}

        with session_scope() as session:
            items = trade_service.get_open_positions_for_user(session, str(telegram_id))
            assets = set((getattr(i.asset, 'value'), getattr(i, 'market', 'Futures')) for i in items)
            tasks = [price_service.get_cached_price(a, m) for a, m in assets]
            prices = await asyncio.gather(*tasks, return_exceptions=True)
            price_map = {a: p for (a, _), p in zip(assets, prices) if isinstance(p, (int, float))}

            out_items = []
            for i in items:
                asset_val = getattr(i.asset, 'value')
                live = price_map.get(asset_val)
                side_val = getattr(i.side, 'value')
                entry_val = _to_decimal(getattr(i.entry, 'value'))
                pnl = _pct(entry_val, live, side_val) if live else 0.0
                
                targets_ui = []
                raw_targets = getattr(i.targets, 'values', [])
                for t in raw_targets:
                    t_price = _to_decimal(getattr(t, 'price'))
                    is_hit = (side_val == "LONG" and live and live >= t_price) or \
                             (side_val == "SHORT" and live and live <= t_price)
                    targets_ui.append({"price": float(t_price), "percent": getattr(t, 'close_percent', 0), "hit": is_hit})

                out_items.append({
                    "id": i.id, "asset": asset_val, "side": side_val, "market": getattr(i, 'market', 'Futures'),
                    "entry": float(entry_val), "stop_loss": float(_to_decimal(getattr(i.stop_loss, 'value'))), 
                    "live_price": live, "pnl_live": pnl,
                    "unified_status": getattr(i, 'unified_status', 'WATCHLIST'),
                    "is_user_trade": getattr(i, 'is_user_trade', False),
                    "leverage": getattr(i, 'leverage', "20x"), 
                    "targets": targets_ui
                })
            return {"ok": True, "portfolio": {"items": out_items}}
    except Exception as e:
        log.error(f"Portfolio Error: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/read-models/trader/{telegram_id}")
async def get_trader_read_model(telegram_id: int, request: Request):
    """Return a versioned, Core-owned read model for one authenticated Web user.

    The actual service authorization is evaluated explicitly to keep the endpoint
    inaccessible from browsers while allowing Web to keep its own session model.
    """
    require_core_service_key(request.headers.get("authorization"))
    trade_service = request.app.state.services.get("trade_service") if request.app.state.services else None
    price_service = request.app.state.services.get("price_service") if request.app.state.services else None
    performance_service = request.app.state.services.get("performance_service") if request.app.state.services else None
    if not trade_service or not price_service or not performance_service:
        raise HTTPException(status_code=503, detail="Core read model is unavailable")

    with session_scope() as session:
        user = UserRepository(session).find_by_telegram_id(telegram_id)
        if not user:
            raise HTTPException(status_code=404, detail="Trader identity was not found")

        positions = trade_service.get_open_positions_for_user(session, str(telegram_id))
        price_requests = [
            price_service.get_cached_price(
                getattr(getattr(position, "asset", None), "value", ""),
                getattr(position, "market", "Futures"),
            )
            for position in positions
        ]
        prices = await asyncio.gather(*price_requests, return_exceptions=True)
        serialized_positions = [
            _serialize_live_position(position, price if isinstance(price, (int, float)) else None)
            for position, price in zip(positions, prices)
        ]
        performance = performance_service.get_trader_performance_report(session, user.id)
        funnel = performance_service.get_trader_funnel_metrics(session, user.id)
        return {
            "ok": True,
            "schema_version": "2026-08-20.1",
            "as_of": datetime.utcnow().isoformat() + "Z",
            "user": {
                "telegram_id": telegram_id,
                "role": getattr(getattr(user, "user_type", None), "value", "TRADER"),
            },
            "portfolio": {
                "open_position_count": len(serialized_positions),
                "positions": serialized_positions,
            },
            "performance": performance,
            "funnel": funnel,
        }


@router.get("/read-models/trader/{telegram_id}/recommendations")
async def get_trader_recommendation_read_model(telegram_id: int, request: Request):
    require_core_service_key(request.headers.get("authorization"))
    with session_scope() as session:
        user = UserRepository(session).find_by_telegram_id(telegram_id)
        if not user:
            raise HTTPException(status_code=404, detail="Trader identity was not found")
        trades = session.execute(
            select(UserTrade).where(UserTrade.user_id == user.id).order_by(UserTrade.created_at.desc(), UserTrade.id.desc()).limit(100)
        ).scalars().all()
        return {
            "ok": True,
            "schema_version": TRADER_RECOMMENDATION_SCHEMA_VERSION,
            "as_of": datetime.utcnow().isoformat() + "Z",
            "items": [_serialize_trade_read_model(trade) for trade in trades],
        }


@router.get("/read-models/trader/{telegram_id}/recommendations/{public_ref:path}")
async def get_owned_trader_recommendation_detail(telegram_id: int, public_ref: str, request: Request):
    """Return one trader-owned UserTrade by public reference.

    The Web service derives telegram_id from its signed session; Core enforces the
    server key and row ownership before returning the detail.
    """
    require_core_service_key(request.headers.get("authorization"))
    normalized_ref = public_ref.strip()
    if not normalized_ref:
        raise HTTPException(status_code=404, detail="Recommendation was not found")
    with session_scope() as session:
        user = UserRepository(session).find_by_telegram_id(telegram_id)
        if not user:
            raise HTTPException(status_code=404, detail="Trader identity was not found")
        trade = _find_owned_user_trade_by_public_ref(session, user.id, normalized_ref)
        if not trade:
            # Do not reveal that a reference exists for another trader.
            raise HTTPException(status_code=404, detail="Recommendation was not found")
        return {
            "ok": True,
            "schema_version": TRADER_RECOMMENDATION_SCHEMA_VERSION,
            "as_of": datetime.utcnow().isoformat() + "Z",
            "item": _serialize_trade_read_model(trade),
        }


@router.get("/read-models/trader/{telegram_id}/historical")
async def get_trader_historical_read_model(telegram_id: int, request: Request):
    require_core_service_key(request.headers.get("authorization"))
    with session_scope() as session:
        user = UserRepository(session).find_by_telegram_id(telegram_id)
        if not user:
            raise HTTPException(status_code=404, detail="Trader identity was not found")
        records = HistoricalSignalQueryService().search(session, trader_user_id=user.id, limit=100)
        return {"ok": True, "as_of": datetime.utcnow().isoformat() + "Z", "items": [_serialize_historical_signal_read_model(signal) for signal in records]}


@router.get("/read-models/analysts")
async def get_analyst_read_model(request: Request):
    require_core_service_key(request.headers.get("authorization"))
    with session_scope() as session:
        rows = AnalystDiscoveryService().find_analysts(session, include_ineligible=True, limit=50)
        items = []
        for row in rows:
            items.append({
                "analyst_code": row["analyst_code"],
                "public_ref": row["public_ref"],
                "public_name": row["public_name"],
                "sample_size": int(row["sample_size"]),
                "win_rate_pct": float(row["win_rate_pct"]),
                "total_pnl_pct": float(row["total_pnl_pct"]),
                "max_drawdown_pct": float(row["max_drawdown_pct"]),
                "active_recommendations": int(row["active_recommendations"]),
                "risk_exposure_pct": float(row["risk_exposure_pct"]),
                "eligible_for_ranking": bool(row["eligible_for_ranking"]),
                "freshness_days": float(row["freshness_days"]) if row["freshness_days"] is not None else None,
            })
        return {"ok": True, "as_of": datetime.utcnow().isoformat() + "Z", "items": items}


@router.post("/historical/intake")
async def create_historical_web_intake(command: HistoricalWebIntakeCommand, request: Request):
    """Stage Web paste/upload items through the existing historical batch/receipt model."""
    require_core_service_key(request.headers.get("authorization"))
    if not command.items or len(command.items) > HistoricalWebIntakeService.MAX_ITEMS:
        raise HTTPException(status_code=422, detail="Historical intake requires between 1 and 5000 items")
    try:
        with session_scope() as session:
            user = UserRepository(session).find_by_telegram_id(command.actor_telegram_id)
            if not user:
                raise HTTPException(status_code=404, detail="Historical intake actor was not found")
            result = HistoricalWebIntakeService().create_batch(
                session,
                requested_by_user_id=user.id,
                source_kind=command.source_kind,
                input_mode=command.input_mode,
                items=[item.model_dump() for item in command.items],
                is_partial=command.is_partial,
                batch_label=command.batch_label,
            )
            return result
    except HistoricalWebIntakeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/historical/intake")
async def list_historical_web_intake(actor_telegram_id: int, request: Request, limit: int = 25):
    require_core_service_key(request.headers.get("authorization"))
    with session_scope() as session:
        user = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if not user:
            raise HTTPException(status_code=404, detail="Historical intake actor was not found")
        try:
            return HistoricalWebIntakeService().list_batches(
                session,
                requested_by_user_id=user.id,
                limit=limit,
            )
        except HistoricalWebIntakeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/historical/intake/{batch_id}/report")
async def get_historical_web_intake_report(batch_id: int, actor_telegram_id: int, request: Request):
    require_core_service_key(request.headers.get("authorization"))
    with session_scope() as session:
        user = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if not user:
            raise HTTPException(status_code=404, detail="Historical intake actor was not found")
        try:
            return HistoricalWebIntakeService().batch_report(
                session,
                batch_id=batch_id,
                requested_by_user_id=user.id,
            )
        except HistoricalWebIntakeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/historical/intake/{batch_id}")
async def get_historical_web_intake(batch_id: int, actor_telegram_id: int, request: Request):
    require_core_service_key(request.headers.get("authorization"))
    with session_scope() as session:
        user = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if not user:
            raise HTTPException(status_code=404, detail="Historical intake actor was not found")
        try:
            return HistoricalWebIntakeService().get_batch(
                session,
                batch_id=batch_id,
                requested_by_user_id=user.id,
            )
        except HistoricalWebIntakeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/owner/review-batches")
async def list_owner_review_batches(actor_telegram_id: int, request: Request):
    require_core_service_key(request.headers.get("authorization"))
    try:
        with session_scope() as session:
            batches = WebCommandService().list_reviewable_batches(session, actor_telegram_id=actor_telegram_id)
            return {"ok": True, "batches": batches}
    except WebCommandError as exc:
        raise HTTPException(status_code=403, detail="Owner command rejected") from exc


@router.post("/owner/review-batches")
async def execute_owner_review(command: OwnerReviewCommand, request: Request):
    require_core_service_key(request.headers.get("authorization"))
    if len(command.idempotency_key.strip()) < 16:
        raise HTTPException(status_code=422, detail="Idempotency key is required")
    try:
        with session_scope() as session:
            return WebCommandService().review_batch(
                session,
                actor_telegram_id=command.actor_telegram_id,
                batch_id=command.batch_id,
                approved=command.approved,
                note=command.note,
                idempotency_key=command.idempotency_key,
            )
    except WebCommandError as exc:
        raise HTTPException(status_code=403, detail="Owner command rejected") from exc


@router.post("/owner/review-batches/{batch_id}/ingest-evidence")
async def execute_evidence_ingestion(batch_id: int, command: EvidenceIngestCommand, request: Request):
    require_core_service_key(request.headers.get("authorization"))
    if batch_id != command.batch_id or len(command.idempotency_key.strip()) < 16:
        raise HTTPException(status_code=422, detail="Invalid evidence ingestion command")
    try:
        with session_scope() as session:
            return WebCommandService().ingest_evidence(
                session,
                actor_telegram_id=command.actor_telegram_id,
                batch_id=command.batch_id,
                idempotency_key=command.idempotency_key,
            )
    except WebCommandError as exc:
        raise HTTPException(status_code=403, detail="Owner command rejected") from exc


@router.post("/owner/historical-signals/{signal_id}/replay-binance")
async def execute_historical_binance_replay(signal_id: int, command: HistoricalBinanceReplayCommand, request: Request):
    # Retired deliberately: the user-facing operation is now batch-derived, so
    # Core alone chooses the reviewed signals and bounded historical time range.
    raise HTTPException(status_code=410, detail="Manual historical replay retired; replay the EVIDENCE_INGESTED batch instead")


@router.post("/owner/historical-drafts/{draft_id}/materialize")
async def execute_historical_draft_materialization(draft_id: int, command: HistoricalDraftMaterializationCommand, request: Request):
    """G5 owner command: materialize an accepted historical draft only, never replay."""
    require_core_service_key(request.headers.get("authorization"))
    if draft_id != command.draft_id or len(command.idempotency_key.strip()) < 16:
        raise HTTPException(status_code=422, detail="Invalid historical draft materialization command")
    try:
        with session_scope() as session:
            return WebCommandService().materialize_accepted_historical_draft(
                session,
                actor_telegram_id=command.actor_telegram_id,
                draft_id=draft_id,
                idempotency_key=command.idempotency_key,
            )
    except WebCommandError as exc:
        detail = str(exc)
        raise HTTPException(status_code=409 if detail.startswith("MATERIALIZATION_BLOCKED") else 403, detail=detail) from exc


@router.post("/owner/review-batches/{batch_id}/replay-binance")
async def execute_reviewed_batch_binance_replay(batch_id: int, command: HistoricalBatchBinanceReplayCommand, request: Request):
    require_core_service_key(request.headers.get("authorization"))
    if batch_id != command.batch_id or len(command.idempotency_key.strip()) < 16:
        raise HTTPException(status_code=422, detail="Invalid historical batch replay command")
    try:
        with session_scope() as session:
            return WebCommandService().replay_reviewed_batch_from_binance(
                session,
                actor_telegram_id=command.actor_telegram_id,
                batch_id=batch_id,
                idempotency_key=command.idempotency_key,
            )
    except WebCommandError as exc:
        raise HTTPException(status_code=409, detail="Historical batch replay rejected") from exc


@router.post("/owner/historical-signals/{signal_id}/g6-replay")
async def execute_g6_historical_replay(signal_id: int, command: HistoricalG6ReplayCommand, request: Request):
    """Run G6 from a G5 materialized HistoricalSignal with Core-derived bounds."""
    require_core_service_key(request.headers.get("authorization"))
    if signal_id != command.signal_id or len(command.idempotency_key.strip()) < 16:
        raise HTTPException(status_code=422, detail="Invalid G6 historical replay command")
    try:
        with session_scope() as session:
            return WebCommandService().replay_g6_historical_signal(
                session,
                actor_telegram_id=command.actor_telegram_id,
                signal_id=signal_id,
                idempotency_key=command.idempotency_key,
            )
    except WebCommandError as exc:
        detail = str(exc)
        status_code = 409 if detail.startswith("G6 replay") else 403
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/owner/operations-feed")
async def get_operations_feed(actor_telegram_id: int, request: Request):
    """Owner-only merged operations feed; financial payloads stay inside Core."""
    require_core_service_key(request.headers.get("authorization"))
    try:
        with session_scope() as session:
            WebCommandService().require_owner(session, actor_telegram_id)
            deliveries = session.execute(select(PublicationDelivery).order_by(PublicationDelivery.updated_at.desc()).limit(50)).scalars().all()
            lifecycle_events = session.execute(select(RecommendationEvent).order_by(RecommendationEvent.event_timestamp.desc()).limit(50)).scalars().all()
            commands = session.execute(select(WebCommandAudit).order_by(WebCommandAudit.created_at.desc()).limit(50)).scalars().all()
            events = []
            for delivery in deliveries:
                severity = "critical" if delivery.status == "FAILED" else "warning" if delivery.status == "RETRY" else "info"
                events.append({"id": f"delivery:{delivery.id}", "category": "PUBLICATION", "code": f"{delivery.operation}:{delivery.status}", "severity": severity, "record_ref": f"REC-{delivery.recommendation_id}", "occurred_at": (delivery.updated_at or delivery.created_at).isoformat()})
            for event in lifecycle_events:
                events.append({"id": f"recommendation:{event.id}", "category": "LIFECYCLE", "code": event.event_type, "severity": "info", "record_ref": f"REC-{event.recommendation_id}", "occurred_at": event.event_timestamp.isoformat()})
            for command_audit in commands:
                events.append({"id": f"command:{command_audit.id}", "category": "AUDIT", "code": command_audit.command_type, "severity": "info", "record_ref": f"BATCH-{command_audit.target_id}", "occurred_at": command_audit.created_at.isoformat()})
            events.sort(key=lambda event: event["occurred_at"], reverse=True)
            trimmed = events[:100]
            return {"ok": True, "events": trimmed, "summary": {"critical": sum(event["severity"] == "critical" for event in trimmed), "warning": sum(event["severity"] == "warning" for event in trimmed), "total": len(trimmed)}}
    except WebCommandError as exc:
        raise HTTPException(status_code=403, detail="Owner command rejected") from exc


@router.get("/owner/r5-readiness")
async def get_r5_readiness(actor_telegram_id: int, request: Request):
    """Owner-only operational R5 snapshot; this endpoint can never enable commerce."""
    require_core_service_key(request.headers.get("authorization"))
    try:
        with session_scope() as session:
            WebCommandService().require_owner(session, actor_telegram_id)
            outbox_backlog = session.execute(
                select(func.count()).select_from(PublicationDelivery).where(PublicationDelivery.status.in_(["PENDING", "PROCESSING", "RETRY", "FAILED"]))
            ).scalar_one()
            review_backlog = session.execute(
                select(func.count()).select_from(HistoricalImportBatch).where(HistoricalImportBatch.status.in_(["DRY_RUN", "OWNER_REVIEW_REQUIRED"]))
            ).scalar_one()
            replay_backlog = session.execute(
                select(func.count()).select_from(HistoricalSignalEvent).where(HistoricalSignalEvent.replay_status == "REPLAY_PENDING")
            ).scalar_one()
            observation = get_r5_observation_status()
            reasons = ["RESTORE_DRILL_DEFERRED"]
            if observation["started_at"] is None:
                reasons.append("NO_R5_OBSERVATION_WINDOW")
            elif not observation["complete"]:
                reasons.append("R5_OBSERVATION_WINDOW_INCOMPLETE")
            if outbox_backlog:
                reasons.append("OUTBOX_NOT_DRAINED")
            if review_backlog:
                reasons.append("OWNER_REVIEW_BACKLOG")
            if replay_backlog:
                reasons.append("REPLAY_PENDING_BACKLOG")
            return {
                "ok": True,
                "status": "HOLD",
                "reasons": reasons,
                "commercial_enabled": False,
                "copy_trading_enabled": False,
                "execution_controls": {
                    "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
                    "trade_live_enabled": settings.TRADE_LIVE_ENABLED,
                },
                "observation": observation,
                "snapshot": {
                    "outbox_backlog": int(outbox_backlog),
                    "owner_review_backlog": int(review_backlog),
                    "replay_backlog": int(replay_backlog),
                },
                "as_of": datetime.utcnow().isoformat() + "Z",
            }
    except WebCommandError as exc:
        raise HTTPException(status_code=403, detail="Owner command rejected") from exc


@router.get("/owner/historical-quality")
async def get_historical_trust_quality(actor_telegram_id: int, request: Request, analyst_id: int | None = None, channel_id: int | None = None):
    """Owner-only, non-financial quality gate for historical trust evidence."""
    require_core_service_key(request.headers.get("authorization"))
    try:
        with session_scope() as session:
            WebCommandService().require_owner(session, actor_telegram_id)
            report = HistoricalReputationService.quality_report(session, analyst_id=analyst_id, channel_id=channel_id)
            return {
                "ok": True,
                "status": "HOLD" if report.rank_eligible_signals == 0 else "EVIDENCE_READY",
                "quality": {
                    "analyst_id": report.analyst_id,
                    "channel_id": report.channel_id,
                    "total_signals": report.total_signals,
                    "verified_signals": report.verified_signals,
                    "rank_eligible_signals": report.rank_eligible_signals,
                    "excluded_signals": report.excluded_signals,
                    "unfilled_signals": report.unfilled_signals,
                    "verified_replay_events": report.verified_replay_events,
                    "market_evidence_artifacts": report.market_evidence_artifacts,
                    "replay_coverage_percent": float(report.replay_coverage_percent),
                    "reviewed_attributions": report.reviewed_attributions,
                    "pending_attributions": report.pending_attributions,
                    "confidence_weighted_sample": float(report.confidence_weighted_sample),
                },
                "commercial_enabled": False,
            }
    except WebCommandError as exc:
        raise HTTPException(status_code=403, detail="Owner command rejected") from exc


@router.get("/owner/historical-trust-readiness")
async def get_historical_trust_readiness(actor_telegram_id: int, request: Request, analyst_id: int | None = None, channel_id: int | None = None):
    """Owner-only fail-closed release gate for public historical ranking."""
    require_core_service_key(request.headers.get("authorization"))
    try:
        with session_scope() as session:
            WebCommandService().require_owner(session, actor_telegram_id)
            readiness = HistoricalTrustReleaseService.evaluate(session, analyst_id=analyst_id, channel_id=channel_id)
            return {
                "ok": True,
                "status": readiness.status,
                "reasons": readiness.reasons,
                "public_ranking_enabled": readiness.public_ranking_enabled,
                "commercial_enabled": readiness.commercial_enabled,
                "snapshot": {
                    "sample_size": readiness.sample_size,
                    "replay_coverage_percent": float(readiness.replay_coverage_percent),
                    "reviewed_attributions": readiness.reviewed_attributions,
                    "pending_attributions": readiness.pending_attributions,
                },
            }
    except WebCommandError as exc:
        raise HTTPException(status_code=403, detail="Owner command rejected") from exc

@router.get("/performance")
async def get_user_performance(initData: str, request: Request):
    """Return Activated-only closed-trade performance for the authenticated user."""
    try:
        user_data = validate_telegram_data(initData, settings.TELEGRAM_BOT_TOKEN)
        service = request.app.state.services.get("performance_service")
        if not service:
            return {"ok": False, "error": "Performance service unavailable"}
        with session_scope() as session:
            user = UserRepository(session).find_by_telegram_id(user_data["id"])
            if not user:
                return {"ok": False, "error": "User not found"}
            report = service.get_trader_performance_report(session, user.id)
            return {"ok": "error" not in report, "report": report}
    except Exception as e:
        log.error(f"Performance Error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


@router.get("/funnel")
async def get_user_funnel(initData: str, request: Request):
    """Return lifecycle conversion metrics for the authenticated user."""
    try:
        user_data = validate_telegram_data(initData, settings.TELEGRAM_BOT_TOKEN)
        service = request.app.state.services.get("performance_service")
        if not service:
            return {"ok": False, "error": "Performance service unavailable"}
        with session_scope() as session:
            user = UserRepository(session).find_by_telegram_id(user_data["id"])
            if not user:
                return {"ok": False, "error": "User not found"}
            metrics = service.get_trader_funnel_metrics(session, user.id)
            return {"ok": "error" not in metrics, "metrics": metrics}
    except Exception as e:
        log.error(f"Funnel Error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


@router.post("/action", status_code=410)
async def retired_legacy_trade_action():
    """Block the unsafe numeric-ID action surface; use typed command endpoints."""
    raise HTTPException(status_code=410, detail="Legacy trade action endpoint is retired")


@router.post("/read-models/trader/{telegram_id}/recommendations/{public_ref:path}/commands/close")
async def close_owned_user_trade(
    telegram_id: int,
    public_ref: str,
    command: UserTradeCloseCommand,
    request: Request,
):
    require_core_service_key(request.headers.get("authorization"))
    if command.actor_telegram_id != telegram_id:
        raise HTTPException(status_code=403, detail="Command actor does not match the trader scope")
    services = request.app.state.services or {}
    lifecycle = services.get("lifecycle_service")
    price_service = services.get("price_service")
    if not lifecycle or not price_service:
        raise HTTPException(status_code=503, detail="UserTrade command services are unavailable")
    try:
        with session_scope() as session:
            return await WebCommandService().close_user_trade(
                session,
                actor_telegram_id=telegram_id,
                public_ref=public_ref,
                idempotency_key=command.idempotency_key,
                lifecycle_service=lifecycle,
                price_service=price_service,
            )
    except WebCommandError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/read-models/trader/{telegram_id}/recommendations/{public_ref:path}/commands/cancel")
async def cancel_owned_pending_user_trade(
    telegram_id: int,
    public_ref: str,
    command: UserTradeCancelCommand,
    request: Request,
):
    require_core_service_key(request.headers.get("authorization"))
    if command.actor_telegram_id != telegram_id:
        raise HTTPException(status_code=403, detail="Command actor does not match the trader scope")
    lifecycle = (request.app.state.services or {}).get("lifecycle_service")
    if not lifecycle:
        raise HTTPException(status_code=503, detail="UserTrade command services are unavailable")
    try:
        with session_scope() as session:
            return await WebCommandService().cancel_pending_user_trade(
                session,
                actor_telegram_id=telegram_id,
                public_ref=public_ref,
                idempotency_key=command.idempotency_key,
                lifecycle_service=lifecycle,
            )
    except WebCommandError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/read-models/trader/{telegram_id}/recommendations/{public_ref:path}/commands/partial-close")
async def partial_close_owned_user_trade(
    telegram_id: int,
    public_ref: str,
    command: UserTradePartialCloseCommand,
    request: Request,
):
    require_core_service_key(request.headers.get("authorization"))
    if command.actor_telegram_id != telegram_id:
        raise HTTPException(status_code=403, detail="Command actor does not match the trader scope")
    services = request.app.state.services or {}
    lifecycle = services.get("lifecycle_service")
    price_service = services.get("price_service")
    if not lifecycle or not price_service:
        raise HTTPException(status_code=503, detail="UserTrade command services are unavailable")
    try:
        with session_scope() as session:
            return await WebCommandService().partial_close_user_trade(
                session,
                actor_telegram_id=telegram_id,
                public_ref=public_ref,
                close_percent=command.close_percent,
                idempotency_key=command.idempotency_key,
                lifecycle_service=lifecycle,
                price_service=price_service,
            )
    except WebCommandError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/read-models/trader/{telegram_id}/recommendations/{public_ref:path}/commands/move-stop-to-breakeven")
async def move_owned_user_trade_stop_to_breakeven(
    telegram_id: int,
    public_ref: str,
    command: UserTradeCloseCommand,
    request: Request,
):
    require_core_service_key(request.headers.get("authorization"))
    if command.actor_telegram_id != telegram_id:
        raise HTTPException(status_code=403, detail="Command actor does not match the trader scope")
    lifecycle = (request.app.state.services or {}).get("lifecycle_service")
    if not lifecycle:
        raise HTTPException(status_code=503, detail="UserTrade command services are unavailable")
    try:
        with session_scope() as session:
            return await WebCommandService().move_user_trade_stop_to_breakeven(
                session,
                actor_telegram_id=telegram_id,
                public_ref=public_ref,
                idempotency_key=command.idempotency_key,
                lifecycle_service=lifecycle,
            )
    except WebCommandError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/read-models/trader/{telegram_id}/recommendations/{public_ref:path}/commands/update-entry")
async def update_owned_pending_user_trade_entry(
    telegram_id: int,
    public_ref: str,
    command: UserTradeEntryUpdateCommand,
    request: Request,
):
    require_core_service_key(request.headers.get("authorization"))
    if command.actor_telegram_id != telegram_id:
        raise HTTPException(status_code=403, detail="Command actor does not match the trader scope")
    lifecycle = (request.app.state.services or {}).get("lifecycle_service")
    if not lifecycle:
        raise HTTPException(status_code=503, detail="UserTrade command services are unavailable")
    try:
        with session_scope() as session:
            return await WebCommandService().update_pending_user_trade_entry(
                session,
                actor_telegram_id=telegram_id,
                public_ref=public_ref,
                entry=command.entry,
                idempotency_key=command.idempotency_key,
                lifecycle_service=lifecycle,
            )
    except WebCommandError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

# ✅ RESTORED: Full Signal Analytics Endpoint
@router.get("/signal/{rec_id}")
async def get_signal_details(rec_id: int, request: Request):
    """
    Provides detailed data for a single signal (for Open Analytics button).
    Includes Live Price, PnL, Targets status, and Event Timeline.
    """
    try:
        lifecycle = request.app.state.services.get("lifecycle_service")
        price_svc = request.app.state.services.get("price_service")

        with session_scope() as session:
            rec_orm = lifecycle.repo.get(session, rec_id)
            if not rec_orm:
                return {"ok": False, "error": "Signal not found"}

            rec = lifecycle.repo._to_entity(rec_orm)
            
            # Fetch Live Price
            live_price = await price_svc.get_cached_price(rec.asset.value, rec.market, force_refresh=False)
            if not live_price:
                 live_price = float(_to_decimal(rec.entry.value))

            # Calculate PnL
            entry_val = _to_decimal(rec.entry.value)
            side_val = rec.side.value
            pnl = _pct(entry_val, live_price, side_val)
            
            # Format Targets
            targets_ui = []
            hit_targets = set()
            # Basic hit detection from events
            for e in rec.events:
                 if "TP" in e.event_type and "HIT" in e.event_type:
                     try: hit_targets.add(int(''.join(filter(str.isdigit, e.event_type))))
                     except: pass
            
            for i, t in enumerate(rec.targets.values, 1):
                t_price = _to_decimal(t.price.value)
                is_hit = i in hit_targets
                # Also check live price if active
                if not is_hit and rec.status == RecommendationStatusEnum.ACTIVE:
                     if side_val == "LONG" and live_price >= t_price: is_hit = True
                     elif side_val == "SHORT" and live_price <= t_price: is_hit = True
                
                targets_ui.append({
                    "price": float(t_price),
                    "roi": round(_pct(entry_val, t_price, side_val), 1),
                    "hit": is_hit
                })

            # Format Timeline
            timeline = []
            for e in rec.events:
                ts_str = e.event_timestamp.strftime("%H:%M")
                desc = e.event_type.replace("_", " ").title()
                if e.event_data and "price" in e.event_data:
                    desc += f" @ {e.event_data['price']}"
                timeline.append({"time": ts_str, "description": desc})

            # Construct Response
            signal_data = {
                "asset": rec.asset.value,
                "side": side_val,
                "entry": float(entry_val),
                "stop_loss": float(_to_decimal(rec.stop_loss.value)),
                "live_price": live_price,
                "pnl": pnl,
                "leverage": "20x", # Placeholder or parse from notes
                "status": rec.status.value,
                "targets": targets_ui,
                "events": timeline[-5:] # Last 5 events
            }
            
            return {"ok": True, "signal": signal_data}

    except Exception as e:
        log.error(f"Signal Detail Error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/api/routers/webapp.py ---
