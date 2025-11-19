#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/trade_service.py ---
# File: src/capitalguard/application/services/trade_service.py
# Version: v33.0.0-R3-FINAL (Postgres Fix + R3 Architecture)
# ✅ THE FIX:
#    1. (Postgres) Replaced invalid SQL 'SELECT DISTINCT ... ORDER BY' with Python-side deduplication
#       in 'get_recent_assets_for_user'.
#    2. (Architecture) Fully aligned with R3 (CreationService, LifecycleService, AlertService).
#    3. (Enum) Removed all references to legacy statuses (STOPPED, TAKE_PROFIT).
# 🎯 IMPACT: Stable, crash-free service compatible with PostgreSQL and the new architecture.

from __future__ import annotations
import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session
from sqlalchemy import func, select, and_
from sqlalchemy.orm import selectinload

# Infrastructure & Domain Imports
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    PublishedMessage, Recommendation, RecommendationEvent, User,
    RecommendationStatusEnum, UserTrade, 
    OrderTypeEnum, ExitStrategyEnum,
    UserTradeStatusEnum, 
    WatchedChannel,
    UserTradeEvent
)
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, ChannelRepository, UserRepository
)
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType, ExitStrategy, UserType as UserTypeEntity
)
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets

# Type-only imports
if False:
    from .alert_service import AlertService
    from .price_service import PriceService
    from .market_data_service import MarketDataService
    from .creation_service import CreationService
    from .lifecycle_service import LifecycleService

logger = logging.getLogger(__name__)

# ---------------------------
# (Internal Helper Functions)
# ---------------------------
def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    if value is None:
        return default
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError):
        return default

def _format_price(price: Any) -> str:
    price_dec = _to_decimal(price)
    return "N/A" if not price_dec.is_finite() else f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    try:
        entry_dec = _to_decimal(entry)
        target_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite():
            return 0.0
        side_upper = (str(side.value) if hasattr(side, 'value') else str(side) or "").upper()
        if side_upper == "LONG":
            pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT":
            pnl = ((entry_dec / target_dec) - 1) * 100
        else:
            return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError):
        return 0.0

def _parse_int_user_id(user_id: Any) -> Optional[int]:
    try:
        if user_id is None:
            return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.lstrip('-').isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None

# ---------------------------
# TradeService Class
# ---------------------------
class TradeService:
    """
    [R3 Facade]
    The central read/write coordinator for trading operations.
    Includes Proxies to CreationService and LifecycleService.
    """
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: "MarketDataService",
        price_service: "PriceService",
        creation_service: "CreationService", # Injected
        lifecycle_service: "LifecycleService" # Injected
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        self.creation_service = creation_service
        self.lifecycle_service = lifecycle_service
        self.alert_service: Optional["AlertService"] = None # Injected later

    # --- Internal DB / Notifier Helpers ---
    
    async def _commit_and_dispatch(self, db_session: Session, orm_object: Union[Recommendation, UserTrade], rebuild_alerts: bool = True):
        # Proxy to LifecycleService logic if needed, or keep local for now as it uses alert_service directly
        # For R3, we keep this logic here as it orchestrates the alert service update
        item_id = getattr(orm_object, 'id', 'N/A')
        item_type = type(orm_object).__name__
        try:
            db_session.commit()
            db_session.refresh(orm_object)
            logger.debug(f"Committed {item_type} ID {item_id}")
        except Exception as commit_err:
            logger.error(f"Commit failed {item_type} ID {item_id}: {commit_err}", exc_info=True)
            db_session.rollback()
            raise

        if isinstance(orm_object, Recommendation):
            rec_orm = orm_object
            if rebuild_alerts and self.alert_service:
                try:
                    logger.info(f"Rebuilding full alert index on request for Rec ID {item_id}...")
                    await self.alert_service.build_triggers_index()
                except Exception as alert_err:
                    logger.exception(f"Alert rebuild fail Rec ID {item_id}: {alert_err}")

            updated_entity = self.repo._to_entity(rec_orm)
            if updated_entity:
                try: await self.notify_card_update(updated_entity, db_session)
                except Exception as notify_err: logger.exception(f"Notify fail Rec ID {item_id}: {notify_err}")
            else: logger.error(f"Failed conv ORM Rec {item_id} to entity")
        
        elif isinstance(orm_object, UserTrade):
             if rebuild_alerts and self.alert_service:
                try:
                    logger.info(f"Rebuilding full alert index on request for UserTrade ID {item_id}...")
                    await self.alert_service.build_triggers_index()
                except Exception as alert_err:
                    logger.exception(f"Alert rebuild fail UserTrade ID {item_id}: {alert_err}")

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        if inspect.iscoroutinefunction(fn): return await fn(*args, **kwargs)
        else: loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        if getattr(rec_entity, "is_shadow", False): return
        try:
            published_messages = self.repo.get_published_messages(db_session, rec_entity.id)
            if not published_messages: return
            tasks = [ self._call_notifier_maybe_async( self.notifier.edit_recommendation_card_by_ids, channel_id=msg.telegram_channel_id, message_id=msg.telegram_message_id, rec=rec_entity) for msg in published_messages ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception): logger.error(f"Notify task fail Rec ID {rec_entity.id}: {res}", exc_info=False)
        except Exception as e: logger.error(f"Error fetch/update pub messages Rec ID {rec_entity.id}: {e}", exc_info=True)

    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or getattr(rec_orm, "is_shadow", False): return
        published_messages = self.repo.get_published_messages(db_session, rec_id)
        for msg in published_messages: asyncio.create_task(self._call_notifier_maybe_async( self.notifier.post_notification_reply, chat_id=msg.telegram_channel_id, message_id=msg.telegram_message_id, text=text ))

    async def _notify_user_trade_update(self, user_id: int, text: str):
        try:
            with session_scope() as session:
                user = UserRepository(session).find_by_id(user_id)
                if not user:
                    logger.warning(f"Cannot notify UserTrade update, user DB ID {user_id} not found.")
                    return
                telegram_user_id = user.telegram_user_id
            
            await self._call_notifier_maybe_async(
                self.notifier.send_private_text, 
                chat_id=telegram_user_id, 
                text=text
            )
        except Exception as e:
            logger.error(f"Failed to send private notification to user {user_id}: {e}", exc_info=True)

    # --- Validation (Proxied to CreationService usually, but kept here for internal use if needed) ---
    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        # This logic is duplicated in CreationService, but TradeService might need it for updates
        side_upper = (str(side) or "").upper()
        if not all(v is not None and isinstance(v, Decimal) and v.is_finite() and v > 0 for v in [entry, stop_loss]): raise ValueError("Entry and SL must be positive finite Decimals.")
        if not targets or not isinstance(targets, list): raise ValueError("Targets must be a non-empty list.")
        target_prices: List[Decimal] = []
        for i, t in enumerate(targets):
            if not isinstance(t, dict) or 'price' not in t: raise ValueError(f"Target {i+1} invalid format.")
            price = _to_decimal(t.get('price'))
            if not price.is_finite() or price <= 0: raise ValueError(f"Target {i+1} price invalid.")
            target_prices.append(price)
        if not target_prices: raise ValueError("No valid target prices found.")
        if side_upper == "LONG" and stop_loss >= entry: raise ValueError("LONG SL must be < Entry.")
        if side_upper == "SHORT" and stop_loss <= entry: raise ValueError("SHORT SL must be > Entry.")
        if side_upper == "LONG" and any(p <= entry for p in target_prices): raise ValueError("LONG targets must be > Entry.")
        if side_upper == "SHORT" and any(p >= entry for p in target_prices): raise ValueError("SHORT targets must be < Entry.")

    # --- Publishing (Internal) ---
    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_db_id: int, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        # This logic is now primarily in CreationService, but kept here if needed by legacy flows or background tasks
        # Actually, background_publish_and_index uses this.
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        channels_to_publish = ChannelRepository(session).list_by_analyst(user_db_id, only_active=True)
        if target_channel_ids is not None: channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
        if not channels_to_publish:
            report["failed"].append({"reason": "No active channels linked/selected."})
            return rec_entity, report
        try:
            from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        except ImportError:
            public_channel_keyboard = lambda *_: None
            logger.warning("public_channel_keyboard not found.")
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        tasks = []
        channel_map = {ch.telegram_channel_id: ch for ch in channels_to_publish}
        for channel_id in channel_map.keys(): tasks.append(self._call_notifier_maybe_async( self.notifier.post_to_channel, channel_id, rec_entity, keyboard ))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, channel_id in enumerate(channel_map.keys()):
            result = results[i]
            if isinstance(result, Exception):
                logger.exception(f"Failed publish Rec {rec_entity.id} channel {channel_id}: {result}")
                report["failed"].append({"channel_id": channel_id, "reason": str(result)})
            elif isinstance(result, tuple) and len(result) == 2:
                session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=result[0], telegram_message_id=result[1]))
                report["success"].append({"channel_id": channel_id, "message_id": result[1]})
            else:
                reason = f"Notifier unexpected result: {type(result)}"
                logger.error(f"Failed publish Rec {rec_entity.id} channel {channel_id}: {reason}")
                report["failed"].append({"channel_id": channel_id, "reason": reason})
        session.flush()
        return rec_entity, report

    # --- Background Publishing Task (ADR-001) ---
    async def background_publish_and_index(
        self, 
        rec_id: int, 
        user_db_id: int, 
        target_channel_ids: Optional[Set[int]] = None
    ):
        logger.info(f"[BG Task Rec {rec_id}]: Starting background publish and index...")
        try:
            with session_scope() as session:
                rec_orm = self.repo.get(session, rec_id)
                if not rec_orm:
                    logger.error(f"[BG Task Rec {rec_id}]: ORM object not found in DB.")
                    return

                rec_entity = self.repo._to_entity(rec_orm)
                if not rec_entity:
                     logger.error(f"[BG Task Rec {rec_id}]: Failed to convert ORM to entity.")
                     return
                
                _, report = await self._publish_recommendation(
                    session, rec_entity, user_db_id, target_channel_ids
                )
                
                success_count = len(report.get("success", []))
                if success_count == 0:
                    logger.warning(f"[BG Task Rec {rec_id}]: Failed to publish to any channel. Report: {report.get('failed')}")
                else:
                    logger.info(f"[BG Task Rec {rec_id}]: Published to {success_count} channels.")

                rec_orm_for_trigger = self.repo.get(session, rec_id) 
                trigger_data = self.alert_service.build_trigger_data_from_orm(rec_orm_for_trigger)

                if trigger_data:
                    await self.alert_service.add_trigger_data(trigger_data)
                else:
                    logger.error(f"[BG Task Rec {rec_id}]: Failed to build trigger data. AlertService will not track this trade!")

                rec_orm.is_shadow = False
                session.commit()
                logger.info(f"[BG Task Rec {rec_id}]: Task complete. Recommendation is now live and indexed.")
                
                await self._notify_user_trade_update(
                    user_id=user_db_id,
                    text=f"✅ **Published!**\nSignal #{rec_orm.asset} is now live in {success_count} channel(s)."
                )

        except Exception as e:
            logger.error(f"[BG Task Rec {rec_id}]: CRITICAL FAILURE in background task: {e}", exc_info=True)
            try:
                 await self._notify_user_trade_update(
                    user_id=user_db_id,
                    text=f"❌ **Publishing Failed!**\nSignal #{rec_id} failed during background processing. Please check admin logs."
                )
            except Exception:
                pass

    # --- Read Utilities (FIXED for Postgres) ---
    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """
        Fetches a list of recently used assets for the user to populate the UI.
        ✅ THE FIX: Use Python-side deduplication instead of DISTINCT + ORDER BY
        to avoid Postgres 'InvalidColumnReference' error.
        """
        user_id_int = self._parse_user_id(user_telegram_id)
        if not user_id_int: return []
        user = UserRepository(db_session).find_by_telegram_id(user_id_int)
        if not user: return []

        assets_in_order = []
        
        if user.user_type == UserTypeEntity.ANALYST:
            # Fetch more than needed to handle duplicates
            recs = (
                db_session.query(Recommendation.asset)
                .filter(Recommendation.analyst_id == user.id)
                .order_by(Recommendation.created_at.desc())
                .limit(limit * 5) 
                .all()
            )
            assets_in_order.extend(r.asset for r in recs)
        else:
            trades = (
                db_session.query(UserTrade.asset)
                .filter(UserTrade.user_id == user.id)
                .order_by(UserTrade.created_at.desc())
                .limit(limit * 5)
                .all()
            )
            assets_in_order.extend(t.asset for t in trades)

        # Deduplicate while preserving order
        asset_list = []
        seen = set()
        for asset in assets_in_order:
            if asset not in seen:
                asset_list.append(asset)
                seen.add(asset)
                if len(asset_list) >= limit:
                    break
        
        # Add defaults if list is short
        if len(asset_list) < limit:
            default_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
            for a in default_assets:
                if a not in asset_list and len(asset_list) < limit:
                    asset_list.append(a)
                    
        return asset_list

    # --- 1. SAFE ATTRIBUTE RESOLVERS (MAINTAINED) ---
    def _resolve_channel_id(self, orm_obj: Any) -> Optional[int]:
        if not orm_obj: return None
        val = getattr(orm_obj, 'source_channel_id', None)
        if val: return val
        val = getattr(orm_obj, 'channel_id', None)
        if val: return val
        if hasattr(orm_obj, 'recommendation') and orm_obj.recommendation:
            rec = orm_obj.recommendation
            return getattr(rec, 'source_channel_id', getattr(rec, 'channel_id', None))
        return None

    def _resolve_recommendation_id(self, trade_obj: Any) -> Optional[int]:
        if not trade_obj: return None
        val = getattr(trade_obj, 'recommendation_id', None)
        if val: return val
        if hasattr(trade_obj, 'recommendation') and trade_obj.recommendation:
            return getattr(trade_obj.recommendation, 'id', None)
        return None

    # --- 3. ENRICHMENT & STATUS MAPPING (FIXED) ---
    def _enrich_entity(self, entity: Any, is_trade: bool, orm_status: Any, channel_id: Optional[int] = None) -> Any:
        if not entity: return None
        entity.is_user_trade = is_trade
        entity.watched_channel_id = channel_id
        
        status_val = orm_status.value if hasattr(orm_status, 'value') else str(orm_status)
        entity.orm_status_value = status_val
        
        if is_trade:
            if status_val == UserTradeStatusEnum.ACTIVATED.value: 
                entity.unified_status = "ACTIVE"
            elif status_val in [UserTradeStatusEnum.WATCHLIST.value, UserTradeStatusEnum.PENDING_ACTIVATION.value]: 
                entity.unified_status = "WATCHLIST"
            else: 
                entity.unified_status = "CLOSED"
        else:
            if status_val == RecommendationStatusEnum.ACTIVE.value: 
                entity.unified_status = "ACTIVE"
            elif status_val == RecommendationStatusEnum.PENDING.value: 
                entity.unified_status = "WATCHLIST"
            else: 
                entity.unified_status = "CLOSED"
                
        return entity

    # --- 4. READ OPERATIONS (FIXED) ---
    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        user_id_int = self._parse_user_id(user_telegram_id)
        if not user_id_int: return []
        user = UserRepository(db_session).find_by_telegram_id(user_id_int)
        if not user: return []
        all_items = []
        tracked_rec_ids = set()

        trader_trades = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trader_trades:
            entity = self.repo._to_entity_from_user_trade(trade)
            if entity:
                safe_channel_id = self._resolve_channel_id(trade)
                rec_id = self._resolve_recommendation_id(trade)
                self._enrich_entity(entity, is_trade=True, orm_status=trade.status, channel_id=safe_channel_id)
                all_items.append(entity)
                if rec_id: tracked_rec_ids.add(rec_id)

        if user.user_type == UserTypeEntity.ANALYST:
            analyst_recs = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in analyst_recs:
                if rec.id in tracked_rec_ids: continue
                entity = self.repo._to_entity(rec)
                if entity:
                    safe_channel_id = self._resolve_channel_id(rec)
                    self._enrich_entity(entity, is_trade=False, orm_status=rec.status, channel_id=safe_channel_id)
                    all_items.append(entity)

        all_items.sort(key=lambda x: x.created_at, reverse=True)
        return all_items

    def get_analyst_history_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 20) -> List[RecommendationEntity]:
        user_id_int = self._parse_user_id(user_telegram_id)
        if not user_id_int: return []
        user = UserRepository(db_session).find_by_telegram_id(user_id_int)
        if not user or user.user_type != UserTypeEntity.ANALYST: return []

        # ✅ THE FIX: Only use the canonical CLOSED status defined in the Domain.
        terminal_statuses = [
            RecommendationStatusEnum.CLOSED,
        ]
        
        recs = (
            db_session.query(Recommendation)
            .filter(Recommendation.analyst_id == user.id)
            .filter(Recommendation.status.in_(terminal_statuses))
            .order_by(Recommendation.created_at.desc())
            .limit(limit)
            .all()
        )

        entities = []
        for r in recs:
            entity = self.repo._to_entity(r)
            if entity:
                safe_channel_id = self._resolve_channel_id(r)
                self._enrich_entity(entity, is_trade=False, orm_status=r.status, channel_id=safe_channel_id)
                entities.append(entity)
        
        return entities

    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        user_id_int = self._parse_user_id(user_telegram_id)
        if not user_id_int: return None
        user = UserRepository(db_session).find_by_telegram_id(user_id_int)
        if not user: return None

        if position_type == 'rec':
            rec_orm = self.repo.get(db_session, position_id)
            if rec_orm and rec_orm.analyst_id == user.id:
                entity = self.repo._to_entity(rec_orm)
                safe_channel_id = self._resolve_channel_id(rec_orm)
                return self._enrich_entity(entity, is_trade=False, orm_status=rec_orm.status, channel_id=safe_channel_id)
                
        elif position_type == 'trade':
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if trade_orm and trade_orm.user_id == user.id:
                entity = self.repo._to_entity_from_user_trade(trade_orm)
                safe_channel_id = self._resolve_channel_id(trade_orm)
                return self._enrich_entity(entity, is_trade=True, orm_status=trade_orm.status, channel_id=safe_channel_id)
        
        return None

    def get_channel_info(self, db_session: Session, channel_id: int) -> Dict[str, Any]:
        try:
            ChannelModel = self.repo.get_watched_channel_model() 
            channel = db_session.query(ChannelModel).filter(ChannelModel.channel_id == channel_id).first()
            return {"id": channel_id, "title": channel.channel_title if channel else "Unknown Channel"}
        except Exception:
            return {"id": channel_id, "title": "Unknown"}

    def get_watched_channels_summary(self, db_session: Session, user_db_id: int) -> List[Dict]:
        return self.repo.get_watched_channels_summary(db_session, user_db_id)

    def _parse_user_id(self, user_id: Any) -> Optional[int]:
        try:
            return int(str(user_id).strip()) if str(user_id).strip().lstrip('-').isdigit() else None
        except:
            return None

    # --- 6. PROXIES ---
    async def create_and_publish_recommendation_async(self, *args, **kwargs): return await self.creation_service.create_and_publish_recommendation_async(*args, **kwargs)
    async def create_trade_from_forwarding_async(self, *args, **kwargs): return await self.creation_service.create_trade_from_forwarding_async(*args, **kwargs)
    async def create_trade_from_recommendation(self, *args, **kwargs): return await self.creation_service.create_trade_from_recommendation(*args, **kwargs)
    
    async def close_user_trade_async(self, *args, **kwargs): return await self.lifecycle_service.close_user_trade_async(*args, **kwargs)
    async def close_recommendation_async(self, *args, **kwargs): return await self.lifecycle_service.close_recommendation_async(*args, **kwargs)
    async def partial_close_async(self, *args, **kwargs): return await self.lifecycle_service.partial_close_async(*args, **kwargs)
    async def update_sl_for_user_async(self, *args, **kwargs): return await self.lifecycle_service.update_sl_for_user_async(*args, **kwargs)
    async def update_targets_for_user_async(self, *args, **kwargs): return await self.lifecycle_service.update_targets_for_user_async(*args, **kwargs)
    async def update_entry_and_notes_async(self, *args, **kwargs): return await self.lifecycle_service.update_entry_and_notes_async(*args, **kwargs)
    async def set_exit_strategy_async(self, *args, **kwargs): return await self.lifecycle_service.set_exit_strategy_async(*args, **kwargs)
    async def move_sl_to_breakeven_async(self, *args, **kwargs): return await self.lifecycle_service.move_sl_to_breakeven_async(*args, **kwargs)
    async def process_invalidation_event(self, *args, **kwargs): return await self.lifecycle_service.process_invalidation_event(*args, **kwargs)
    async def process_activation_event(self, *args, **kwargs): return await self.lifecycle_service.process_activation_event(*args, **kwargs)
    async def process_sl_hit_event(self, *args, **kwargs): return await self.lifecycle_service.process_sl_hit_event(*args, **kwargs)
    async def process_tp_hit_event(self, *args, **kwargs): return await self.lifecycle_service.process_tp_hit_event(*args, **kwargs)
    async def process_user_trade_activation_event(self, *args, **kwargs): return await self.lifecycle_service.process_user_trade_activation_event(*args, **kwargs)
    async def process_user_trade_invalidation_event(self, *args, **kwargs): return await self.lifecycle_service.process_user_trade_invalidation_event(*args, **kwargs)
    async def process_user_trade_sl_hit_event(self, *args, **kwargs): return await self.lifecycle_service.process_user_trade_sl_hit_event(*args, **kwargs)
    async def process_user_trade_tp_hit_event(self, *args, **kwargs): return await self.lifecycle_service.process_user_trade_tp_hit_event(*args, **kwargs)
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/trade_service.py ---