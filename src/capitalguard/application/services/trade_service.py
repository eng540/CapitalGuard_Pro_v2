#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/trade_service.py ---
# File: src/capitalguard/application/services/trade_service.py
# Version: v3.1.3-R3-FIX (Enum Crash Fix)
# ✅ THE FIX: (RCA-001) Removed references to non-existent Enum members (STOPPED, TAKE_PROFIT).
#    - Aligned 'get_analyst_history_for_user' with Domain Entity 'RecommendationStatus'.
#    - Added safe fallback for status mapping to prevent future crashes.
# 🎯 IMPACT: Prevents AttributeError and ensures strict adherence to Domain definitions.

from __future__ import annotations
import logging
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session
from sqlalchemy import or_

# Infrastructure Imports
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.domain.entities import Recommendation as RecommendationEntity
from capitalguard.infrastructure.db.models import (
    UserTrade, Recommendation, UserTradeStatusEnum, 
    RecommendationStatusEnum, UserType as UserTypeEntity
)

# Service Imports (Type Hinting Only)
from .creation_service import CreationService
from .lifecycle_service import LifecycleService
if False:
    from .price_service import PriceService
    from .market_data_service import MarketDataService

logger = logging.getLogger(__name__)

class TradeService:
    """
    [R2-FINAL Facade]
    The central read/write coordinator for trading operations.
    Includes Proxies to CreationService and LifecycleService.
    """
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: "MarketDataService",
        price_service: "PriceService",
        creation_service: "CreationService",
        lifecycle_service: "LifecycleService"
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        self.creation_service = creation_service
        self.lifecycle_service = lifecycle_service
        self.alert_service = None

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

    # --- 2. VALIDATION LOGIC (MAINTAINED) ---
    def _validate_recommendation_data(self, data: Dict[str, Any], is_rec: bool = True) -> Dict[str, str]:
        errors: Dict[str, str] = {}
        required_fields = ['asset', 'side', 'entry', 'stop_loss']
        for field in required_fields:
            if data.get(field) is None: errors[field] = f"Missing required field: {field}"
        if errors: return errors
        try:
            side = str(data.get('side', '')).upper()
            entry = Decimal(str(data.get('entry')))
            sl = Decimal(str(data.get('stop_loss')))
            if side == 'LONG':
                if sl >= entry: errors['sl_consistency'] = "For LONG, Stop Loss must be lower than Entry."
            elif side == 'SHORT':
                if sl <= entry: errors['sl_consistency'] = "For SHORT, Stop Loss must be higher than Entry."
        except Exception as e: errors['system_error'] = f"Validation system error: {str(e)}"
        return errors

    # --- 3. ENRICHMENT & STATUS MAPPING (FIXED) ---
    def _enrich_entity(self, entity: Any, is_trade: bool, orm_status: Any, channel_id: Optional[int] = None) -> Any:
        if not entity: return None
        entity.is_user_trade = is_trade
        entity.watched_channel_id = channel_id
        
        # Safe status extraction
        status_val = orm_status.value if hasattr(orm_status, 'value') else str(orm_status)
        entity.orm_status_value = status_val
        
        # Robust mapping with fallback
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
                # Default fallback for CLOSED or any unknown state
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
        """
        Fetches historical (closed) recommendations for an analyst.
        ✅ FIX: Removed references to non-existent Enum members (STOPPED, TAKE_PROFIT).
        """
        user_id_int = self._parse_user_id(user_telegram_id)
        if not user_id_int: return []
        user = UserRepository(db_session).find_by_telegram_id(user_id_int)
        if not user or user.user_type != UserTypeEntity.ANALYST: return []

        # ✅ THE FIX: Only use the canonical CLOSED status defined in the Domain.
        # The outcome (Win/Loss) is determined by PnL, not the status itself.
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

    # --- 5. UTILITIES ---
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
    
    # ✅ NEW: Helper for asset pre-fetching (used by conversation handlers)
    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str) -> List[str]:
        """
        Fetches a list of recently used assets for the user to populate the UI.
        """
        user_id_int = self._parse_user_id(user_telegram_id)
        if not user_id_int: return []
        user = UserRepository(db_session).find_by_telegram_id(user_id_int)
        if not user: return []

        # Query distinct assets from recent recommendations
        recent_recs = (
            db_session.query(Recommendation.asset)
            .filter(Recommendation.analyst_id == user.id)
            .order_by(Recommendation.created_at.desc())
            .limit(10)
            .distinct()
            .all()
        )
        
        # Flatten list
        assets = [r[0] for r in recent_recs if r[0]]
        
        # Add defaults if list is short
        defaults = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        for d in defaults:
            if d not in assets:
                assets.append(d)
                
        return assets[:6] # Return top 6

    # --- 6. PROXIES ---
    async def create_and_publish_recommendation_async(self, *args, **kwargs): return await self.creation_service.create_and_publish_recommendation_async(*args, **kwargs)
    async def background_publish_and_index(self, *args, **kwargs): return await self.creation_service.background_publish_and_index(*args, **kwargs)
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