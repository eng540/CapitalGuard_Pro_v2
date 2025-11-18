# File: src/capitalguard/application/services/trade_service.py
# Version: v3.0.8-R2-FINAL (Production Fix - Safe Channel Resolution)
# ✅ STATUS: STABLE & CRASH-PROOF
#    - Fixed AttributeError: 'UserTrade' has no source_channel_id.
#    - Implemented safe channel resolution logic (Trade -> Rec -> Channel).
#    - Finalized Unified Status Mapping.

from __future__ import annotations
import logging
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session

# Infrastructure Imports
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.domain.entities import Recommendation as RecommendationEntity
from capitalguard.infrastructure.db.models import (
    UserTrade, Recommendation, UserTradeStatusEnum, 
    RecommendationStatusEnum, UserType as UserTypeEntity
)

# Service Imports (Type hinting only)
if False:
    from .creation_service import CreationService
    from .lifecycle_service import LifecycleService
    from .price_service import PriceService
    from .market_data_service import MarketDataService

logger = logging.getLogger(__name__)

class TradeService:
    """
    [R2-FINAL Facade]
    Central coordinator for trade operations. Single Source of Truth.
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

    # --- 1. HELPER: SAFE CHANNEL RESOLUTION (THE FIX) ---
    
    def _resolve_channel_id(self, orm_obj: Any) -> Optional[int]:
        """
        Safely resolves the channel ID from a Trade or Recommendation object
        without causing AttributeErrors.
        """
        if not orm_obj:
            return None
            
        # 1. Try direct attributes (common naming conventions)
        if hasattr(orm_obj, 'source_channel_id') and orm_obj.source_channel_id:
            return orm_obj.source_channel_id
        if hasattr(orm_obj, 'channel_id') and orm_obj.channel_id:
            return orm_obj.channel_id
            
        # 2. If it's a UserTrade, try to get it from the parent Recommendation
        if hasattr(orm_obj, 'recommendation') and orm_obj.recommendation:
            rec = orm_obj.recommendation
            if hasattr(rec, 'source_channel_id') and rec.source_channel_id:
                return rec.source_channel_id
            if hasattr(rec, 'channel_id') and rec.channel_id:
                return rec.channel_id
                
        return None

    # --- 2. VALIDATION LOGIC ---

    def _validate_recommendation_data(self, data: Dict[str, Any], is_rec: bool = True) -> Dict[str, str]:
        """Performs rigorous validation on recommendation data."""
        errors: Dict[str, str] = {}
        required_fields = ['asset', 'side', 'entry', 'stop_loss']
        for field in required_fields:
            if data.get(field) is None:
                errors[field] = f"Missing required field: {field}"
        
        if errors: return errors

        try:
            asset = str(data.get('asset', '')).upper()
            side = str(data.get('side', '')).upper()
            if not asset: errors['asset'] = "Asset symbol cannot be empty."
            if side not in ['LONG', 'SHORT']: errors['side'] = "Side must be 'LONG' or 'SHORT'."

            entry = Decimal(str(data.get('entry')))
            sl = Decimal(str(data.get('stop_loss')))
            
            if entry <= 0: errors['entry'] = "Entry price must be positive."
            if sl <= 0: errors['stop_loss'] = "Stop Loss price must be positive."

            if side == 'LONG':
                if sl >= entry: errors['sl_consistency'] = "For LONG, Stop Loss must be lower than Entry."
            elif side == 'SHORT':
                if sl <= entry: errors['sl_consistency'] = "For SHORT, Stop Loss must be higher than Entry."

        except (InvalidOperation, ValueError, TypeError) as e:
            errors['data_integrity'] = f"Invalid numeric format: {str(e)}"
        except Exception as e:
            errors['system_error'] = f"Validation system error: {str(e)}"

        return errors

    def _validate_recommendation_data_legacy(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict]) -> None:
        data = {'asset': 'UNKNOWN', 'side': side, 'entry': entry, 'stop_loss': stop_loss, 'targets': targets}
        if data['asset'] == 'UNKNOWN': data['asset'] = 'BTCUSDT' 
        errors = self._validate_recommendation_data(data, is_rec=True)
        if 'asset' in errors and errors['asset'] == "Asset symbol cannot be empty.": del errors['asset']
        if errors: raise ValueError(f"Validation Failed: {errors}")

    # --- 3. DATA ENRICHMENT & UNIFIED STATUS ---

    def _enrich_entity(self, entity: Any, is_trade: bool, orm_status: Any, channel_id: Optional[int] = None) -> Any:
        """
        Applies Unified Status logic and flags. Single Source of Truth.
        """
        if not entity: return None
        
        entity.is_user_trade = is_trade
        entity.watched_channel_id = channel_id
        
        status_val = orm_status.value if hasattr(orm_status, 'value') else str(orm_status)
        entity.orm_status_value = status_val

        # Unified Status Mapping Table
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

    # --- 4. READ OPERATIONS (CRASH FIX APPLIED) ---

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """Retrieves 'ACTIVE' and 'WATCHLIST' items."""
        user_id_int = self._parse_user_id(user_telegram_id)
        if not user_id_int: return []
        
        user = UserRepository(db_session).find_by_telegram_id(user_id_int)
        if not user: return []

        all_items = []
        tracked_rec_ids = set()

        # A. User Trades
        trader_trades = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trader_trades:
            entity = self.repo._to_entity_from_user_trade(trade)
            if entity:
                # ✅ FIX: Use safe resolution instead of direct attribute access
                safe_channel_id = self._resolve_channel_id(trade)
                
                self._enrich_entity(entity, is_trade=True, orm_status=trade.status, channel_id=safe_channel_id)
                all_items.append(entity)
                if trade.recommendation_id:
                    tracked_rec_ids.add(trade.recommendation_id)

        # B. Analyst Recommendations
        if user.user_type == UserTypeEntity.ANALYST:
            analyst_recs = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in analyst_recs:
                if rec.id in tracked_rec_ids: continue # Deduplicate
                
                entity = self.repo._to_entity(rec)
                if entity:
                    # ✅ FIX: Use safe resolution
                    safe_channel_id = self._resolve_channel_id(rec)
                    
                    self._enrich_entity(entity, is_trade=False, orm_status=rec.status, channel_id=safe_channel_id)
                    all_items.append(entity)

        all_items.sort(key=lambda x: x.created_at, reverse=True)
        return all_items

    def get_analyst_history_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 20) -> List[RecommendationEntity]:
        """Retrieves 'CLOSED' items for Analyst."""
        user_id_int = self._parse_user_id(user_telegram_id)
        if not user_id_int: return []
        
        user = UserRepository(db_session).find_by_telegram_id(user_id_int)
        if not user or user.user_type != UserTypeEntity.ANALYST: return []

        recs = (
            db_session.query(Recommendation)
            .filter(Recommendation.analyst_id == user.id)
            .filter(Recommendation.status.in_([
                RecommendationStatusEnum.CLOSED, 
                RecommendationStatusEnum.STOPPED, 
                RecommendationStatusEnum.TAKE_PROFIT
            ]))
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
        """Fetches single item details."""
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

    # --- 5. UTILITIES & PROXIES ---

    def get_channel_info(self, db_session: Session, channel_id: int) -> Dict[str, Any]:
        """Safe channel info retrieval."""
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

    # --- 6. WRITE PROXIES (Unchanged) ---
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