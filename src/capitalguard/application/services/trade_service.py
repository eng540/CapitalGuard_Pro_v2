# File: src/capitalguard/application/services/trade_service.py
# Version: v3.0.4-R2 (Entity Flags Fix)
# ✅ THE FIX: (Critical Priority 1)
#    - 1. (LOGIC) ضبط get_open_positions_for_user لتعيين is_user_trade=False لتوصيات المحلل.
#    - 2. (LOGIC) ضبط get_position_details_for_user لتعيين نفس العلامة.
# 🎯 IMPACT: الواجهة تميز الآن بين التوصية والصفقة وتعرض لوحة التحكم الصحيحة.

from __future__ import annotations
import logging
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

# Infrastructure & Domain Imports
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.domain.entities import Recommendation as RecommendationEntity
from capitalguard.infrastructure.db.models import Recommendation, UserTrade, UserTradeStatusEnum, RecommendationStatusEnum
from capitalguard.domain.entities import UserType as UserTypeEntity, RecommendationStatus as RecommendationStatusEntity
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets


# Import new R2 services
from .creation_service import CreationService
from .lifecycle_service import LifecycleService

# Type-only imports (for type hints)
if False:
    from .alert_service import AlertService
    from .price_service import PriceService
    from .market_data_service import MarketDataService

logger = logging.getLogger(__name__)

# --- Helper Functions (Utilities) ---

def _parse_int_user_id(user_id: Any) -> Optional[int]:
    """Safely parses a user ID to int."""
    try:
        if user_id is None:
            return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.lstrip('-').isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None
        
def _validate_recommendation_data(data: Dict[str, Any], is_rec: bool = True) -> Dict[str, str]:
    """
    Validates core recommendation data integrity (Entry vs SL consistency).
    Returns a dictionary of errors. Empty dict means success.
    """
    errors: Dict[str, str] = {}
    required_fields = ['asset', 'side', 'entry', 'stop_loss']
    for field in required_fields:
        if data.get(field) is None:
            errors[field] = f"Missing required field: {field}"
            
    try:
        entry = data.get('entry')
        sl = data.get('stop_loss')
        side = data.get('side')
        
        if entry is None or sl is None or side is None:
            return errors
        
        if not isinstance(entry, Decimal): entry = Decimal(str(entry))
        if not isinstance(sl, Decimal): sl = Decimal(str(sl))
             
        if entry <= Decimal('0') or sl <= Decimal('0'):
            errors['price_value'] = "Entry and Stop Loss prices must be positive."
            return errors

        if side.upper() == 'LONG':
            if entry <= sl:
                errors['sl_consistency'] = "For LONG, Entry price must be higher than Stop Loss price."
        elif side.upper() == 'SHORT':
            if entry >= sl:
                errors['sl_consistency'] = "For SHORT, Entry price must be lower than Stop Loss price."

    except Exception as e:
        errors['price_conversion'] = f"Error converting prices for validation: {e}"
        
    return errors


class TradeService:
    """
    [R2 Facade]
    واجهة موحدة لخدمات التداول.
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
        self.alert_service: Optional["AlertService"] = None

    def _validate_recommendation_data(self, data: Dict[str, Any], is_rec: bool = True) -> Dict[str, str]:
        """Proxy to the validation utility."""
        return _validate_recommendation_data(data, is_rec)

    def _validate_recommendation_data_legacy(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict]) -> None:
        """[Compatibility Layer]"""
        data = {'side': side, 'entry': entry, 'stop_loss': stop_loss, 'targets': targets}
        errors = self._validate_recommendation_data(data, is_rec=True)
        if errors: raise ValueError(f"Validation Errors: {errors}")
    
    # --- CreationService Proxies ---
    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        return await self.creation_service.create_and_publish_recommendation_async(user_id, db_session, **kwargs)

    async def background_publish_and_index(self, rec_id: int, user_db_id: int, target_channel_ids: Optional[Set[int]] = None):
        return await self.creation_service.background_publish_and_index(rec_id, user_db_id, target_channel_ids)

    async def create_trade_from_forwarding_async(self, *args, **kwargs) -> Dict[str, Any]:
        return await self.creation_service.create_trade_from_forwarding_async(*args, **kwargs)

    async def create_trade_from_recommendation(self, *args, **kwargs) -> Dict[str, Any]:
        return await self.creation_service.create_trade_from_recommendation(*args, **kwargs)

    # --- LifecycleService Proxies ---
    async def close_user_trade_async(self, *args, **kwargs):
        return await self.lifecycle_service.close_user_trade_async(*args, **kwargs)

    async def close_recommendation_async(self, *args, **kwargs):
        return await self.lifecycle_service.close_recommendation_async(*args, **kwargs)

    async def partial_close_async(self, *args, **kwargs):
        return await self.lifecycle_service.partial_close_async(*args, **kwargs)

    async def update_sl_for_user_async(self, *args, **kwargs):
        return await self.lifecycle_service.update_sl_for_user_async(*args, **kwargs)

    async def update_targets_for_user_async(self, *args, **kwargs):
        return await self.lifecycle_service.update_targets_for_user_async(*args, **kwargs)

    async def update_entry_and_notes_async(self, *args, **kwargs):
        return await self.lifecycle_service.update_entry_and_notes_async(*args, **kwargs)

    async def set_exit_strategy_async(self, *args, **kwargs):
        return await self.lifecycle_service.set_exit_strategy_async(*args, **kwargs)

    async def move_sl_to_breakeven_async(self, *args, **kwargs):
        return await self.lifecycle_service.move_sl_to_breakeven_async(*args, **kwargs)

    # --- Event Handler Proxies ---
    async def process_invalidation_event(self, *args, **kwargs):
        return await self.lifecycle_service.process_invalidation_event(*args, **kwargs)

    async def process_activation_event(self, *args, **kwargs):
        return await self.lifecycle_service.process_activation_event(*args, **kwargs)

    async def process_sl_hit_event(self, *args, **kwargs):
        return await self.lifecycle_service.process_sl_hit_event(*args, **kwargs)

    async def process_tp_hit_event(self, *args, **kwargs):
        return await self.lifecycle_service.process_tp_hit_event(*args, **kwargs)

    async def process_user_trade_activation_event(self, *args, **kwargs):
        return await self.lifecycle_service.process_user_trade_activation_event(*args, **kwargs)
    
    async def process_user_trade_invalidation_event(self, *args, **kwargs):
        return await self.lifecycle_service.process_user_trade_invalidation_event(*args, **kwargs)

    async def process_user_trade_sl_hit_event(self, *args, **kwargs):
        return await self.lifecycle_service.process_user_trade_sl_hit_event(*args, **kwargs)

    async def process_user_trade_tp_hit_event(self, *args, **kwargs):
        return await self.lifecycle_service.process_user_trade_tp_hit_event(*args, **kwargs)


    # --- Legacy Read Functions (to be refactored into a 'ReadService') ---
    
    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """
        Fetches all open positions (Analyst Recs + User Trades) for a user.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: 
            return []
        
        all_items = []
        
        # 1. Get Trader's personal trades
        trader_trades = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trader_trades:
            entity = self.repo._to_entity_from_user_trade(trade)
            if entity:
                all_items.append(entity)

        # 2. If user is also an analyst, get their official recommendations
        if user.user_type == UserTypeEntity.ANALYST:
            analyst_recs = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in analyst_recs:
                # Avoid duplicates if analyst is tracking their own signal
                is_tracked = any(item.id == rec.id and item.is_user_trade is False for item in all_items if hasattr(item, 'is_user_trade'))
                if not is_tracked:
                    entity = self.repo._to_entity(rec)
                    if entity:
                        # ✅ FIX: Explicitly mark as Analyst Rec
                        entity.is_user_trade = False 
                        all_items.append(entity)
                        
        # Sort by creation time descending
        all_items.sort(key=lambda x: x.created_at, reverse=True)
        return all_items


    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """
        Fetches details for a *single* position, checking for ownership.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: 
            return None

        if position_type == 'rec':
            rec_orm = self.repo.get(db_session, position_id)
            if rec_orm and rec_orm.analyst_id == user.id:
                entity = self.repo._to_entity(rec_orm)
                # ✅ FIX: Explicitly mark as Analyst Rec
                if entity: entity.is_user_trade = False
                return entity
        elif position_type == 'trade':
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if trade_orm and trade_orm.user_id == user.id:
                return self.repo._to_entity_from_user_trade(trade_orm)
        
        return None
        
    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """
        Fetches most recent assets used by this user (Analyst Recs or User Trades).
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        assets_in_order = []
        
        if not user:
            return []
        
        if user.user_type == UserTypeEntity.ANALYST:
            recs = (
                db_session.query(Recommendation.asset, Recommendation.created_at)
                .filter(Recommendation.analyst_id == user.id)
                .order_by(Recommendation.created_at.desc())
                .limit(limit * 5) 
                .all()
            )
            assets_in_order.extend(r.asset for r in recs)
        
        # Also include user trades (even for analysts)
        trades = (
            db_session.query(UserTrade.asset, UserTrade.created_at)
            .filter(UserTrade.user_id == user.id)
            .order_by(UserTrade.created_at.desc())
            .limit(limit * 5)
            .all()
        )
        assets_in_order.extend(t.asset for t in trades)
        
        # Get unique assets while preserving recent order
        asset_list = []
        seen = set()
        for asset in assets_in_order:
            if asset not in seen:
                asset_list.append(asset)
                seen.add(asset)
                if len(asset_list) >= limit:
                    break
                    
        if len(asset_list) < limit:
            default_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
            for a in default_assets:
                if a not in asset_list and len(asset_list) < limit:
                    asset_list.append(a)
                    
        return asset_list