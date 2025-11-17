# File: src/capitalguard/application/services/trade_service.py
# Version: v3.0.1-R2 (NameError Hotfix)
# ✅ THE FIX: (R2 Architecture - Hotfix)
#    - 1. (CRITICAL) إصلاح `NameError: name 'Recommendation' is not defined`
#       الذي يحدث عند استدعاء `get_recent_assets_for_user`.
#    - 2. (NEW) إضافة `from capitalguard.infrastructure.db.models import Recommendation, UserTrade`.
#    - 3. (NEW) إضافة `from capitalguard.domain.entities import UserType as UserTypeEntity`.
# 🎯 IMPACT: هذا الإصلاح يحل الـ `NameError` ويجعل دالة `get_recent_assets_for_user`
#    (التي يستدعيها `/newrec`) قادرة على العمل.

from __future__ import annotations
import logging
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

# Infrastructure & Domain Imports
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.domain.entities import Recommendation as RecommendationEntity
# ✅ R2 Hotfix: Add missing imports needed by legacy read functions
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

# --- Helper Functions (فقط الدوال المتبقية التي تحتاجها الواجهة) ---

def _parse_int_user_id(user_id: Any) -> Optional[int]:
    """Safely parses a user ID to int."""
    try:
        if user_id is None:
            return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.lstrip('-').isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None
        
# --- (All other helpers like _to_decimal, _pct, _validate moved to services) ---


class TradeService:
    """
    [R2 Facade]
    واجهة موحدة لخدمات التداول.
    لا تحتوي على أي منطق أعمال؛ بل تقوم فقط بتوجيه الاستدعاءات
    إلى الخدمات المتخصصة (CreationService و LifecycleService).
    """
    def __init__(
        self,
        # (Dependencies for legacy read functions)
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: "MarketDataService",
        price_service: "PriceService",
        
        # ✅ R2: Injected Services
        creation_service: "CreationService",
        lifecycle_service: "LifecycleService"
    ):
        # Dependencies for legacy functions
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        
        # ✅ R2: Services
        self.creation_service = creation_service
        self.lifecycle_service = lifecycle_service
        
        # Circular dependency injection
        self.alert_service: Optional["AlertService"] = None

    # --- CreationService Proxies ---

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """[Proxy] توجيه إنشاء التوصية إلى CreationService."""
        logger.debug(f"TradeService (Facade) proxying 'create_and_publish' to CreationService for user {user_id}")
        return await self.creation_service.create_and_publish_recommendation_async(user_id, db_session, **kwargs)

    async def background_publish_and_index(self, rec_id: int, user_db_id: int, target_channel_ids: Optional[Set[int]] = None):
        """[Proxy] توجيه النشر الخلفي إلى CreationService."""
        logger.debug(f"TradeService (Facade) proxying 'background_publish' to CreationService for Rec {rec_id}")
        return await self.creation_service.background_publish_and_index(rec_id, user_db_id, target_channel_ids)

    async def create_trade_from_forwarding_async(self, *args, **kwargs) -> Dict[str, Any]:
        """[Proxy] توجيه إنشاء صفقة المتداول إلى CreationService."""
        logger.debug(f"TradeService (Facade) proxying 'create_trade_from_forwarding' to CreationService")
        return await self.creation_service.create_trade_from_forwarding_async(*args, **kwargs)

    async def create_trade_from_recommendation(self, *args, **kwargs) -> Dict[str, Any]:
        """[Proxy] توجيه تفعيل توصية رسمية إلى CreationService."""
        logger.debug(f"TradeService (Facade) proxying 'create_trade_from_recommendation' to CreationService")
        return await self.creation_service.create_trade_from_recommendation(*args, **kwargs)

    # --- LifecycleService Proxies ---
    
    async def close_user_trade_async(self, *args, **kwargs):
        """[Proxy] توجيه إغلاق صفقة المتداول إلى LifecycleService."""
        logger.debug(f"TradeService (Facade) proxying 'close_user_trade' to LifecycleService")
        return await self.lifecycle_service.close_user_trade_async(*args, **kwargs)

    async def close_recommendation_async(self, *args, **kwargs):
        """[Proxy] توجيه إغلاق توصية المحلل إلى LifecycleService."""
        logger.debug(f"TradeService (Facade) proxying 'close_recommendation' to LifecycleService")
        return await self.lifecycle_service.close_recommendation_async(*args, **kwargs)

    async def partial_close_async(self, *args, **kwargs):
        """[Proxy] توجيه الإغلاق الجزئي إلى LifecycleService."""
        return await self.lifecycle_service.partial_close_async(*args, **kwargs)

    async def update_sl_for_user_async(self, *args, **kwargs):
        """[Proxy] توجيه تحديث SL إلى LifecycleService."""
        return await self.lifecycle_service.update_sl_for_user_async(*args, **kwargs)

    async def update_targets_for_user_async(self, *args, **kwargs):
        """[Proxy] توجيه تحديث TPs إلى LifecycleService."""
        return await self.lifecycle_service.update_targets_for_user_async(*args, **kwargs)

    async def update_entry_and_notes_async(self, *args, **kwargs):
        """[Proxy] توجيه تحديث الدخول/الملاحظات إلى LifecycleService."""
        return await self.lifecycle_service.update_entry_and_notes_async(*args, **kwargs)

    async def set_exit_strategy_async(self, *args, **kwargs):
        """[Proxy] توجيه ضبط استراتيجية الخروج إلى LifecycleService."""
        return await self.lifecycle_service.set_exit_strategy_async(*args, **kwargs)

    async def move_sl_to_breakeven_async(self, *args, **kwargs):
        """[Proxy] توجيه نقل SL إلى الدخول إلى LifecycleService."""
        return await self.lifecycle_service.move_sl_to_breakeven_async(*args, **kwargs)

    # --- Event Handler Proxies (Called by AlertService) ---

    async def process_invalidation_event(self, *args, **kwargs):
        """[Proxy Event] توجيه حدث الإلغاء إلى LifecycleService."""
        return await self.lifecycle_service.process_invalidation_event(*args, **kwargs)

    async def process_activation_event(self, *args, **kwargs):
        """[Proxy Event] توجيه حدث التفعيل إلى LifecycleService."""
        return await self.lifecycle_service.process_activation_event(*args, **kwargs)

    async def process_sl_hit_event(self, *args, **kwargs):
        """[Proxy Event] توجيه حدث SL إلى LifecycleService."""
        return await self.lifecycle_service.process_sl_hit_event(*args, **kwargs)

    async def process_tp_hit_event(self, *args, **kwargs):
        """[Proxy Event] توجيه حدث TP إلى LifecycleService."""
        return await self.lifecycle_service.process_tp_hit_event(*args, **kwargs)

    async def process_user_trade_activation_event(self, *args, **kwargs):
        """[Proxy Event] توجيه تفعيل صفقة المتداول إلى LifecycleService."""
        return await self.lifecycle_service.process_user_trade_activation_event(*args, **kwargs)
    
    async def process_user_trade_invalidation_event(self, *args, **kwargs):
        """[Proxy Event] توجيه إلغاء صفقة المتداول إلى LifecycleService."""
        return await self.lifecycle_service.process_user_trade_invalidation_event(*args, **kwargs)

    async def process_user_trade_sl_hit_event(self, *args, **kwargs):
        """[Proxy Event] توجيه SL صفقة المتداول إلى LifecycleService."""
        return await self.lifecycle_service.process_user_trade_sl_hit_event(*args, **kwargs)

    async def process_user_trade_tp_hit_event(self, *args, **kwargs):
        """[Proxy Event] توجيه TP صفقة المتداول إلى LifecycleService."""
        return await self.lifecycle_service.process_user_trade_tp_hit_event(*args, **kwargs)


    # --- Legacy Read Functions (to be refactored into a 'ReadService') ---
    
    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """
        Fetches all open positions (Analyst Recs + User Trades) for a user.
        This is a complex query merging two different concepts.
        """
        logger.debug(f"TradeService (Facade) executing legacy 'get_open_positions_for_user'")
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: 
            return []
        
        all_items = []
        
        # 1. Get Trader's personal trades
        trader_trades = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trader_trades:
            # Convert UserTrade ORM to a RecommendationEntity-like object
            # ✅ CRITICAL: This is the function that is missing in repository.py
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
                        all_items.append(entity)
                        
        # Sort by creation time descending
        all_items.sort(key=lambda x: x.created_at, reverse=True)
        return all_items


    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """
        Fetches details for a *single* position, checking for ownership.
        """
        logger.debug(f"TradeService (Facade) executing legacy 'get_position_details_for_user'")
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: 
            return None

        if position_type == 'rec':
            rec_orm = self.repo.get(db_session, position_id)
            if rec_orm and rec_orm.analyst_id == user.id:
                return self.repo._to_entity(rec_orm)
        elif position_type == 'trade':
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if trade_orm and trade_orm.user_id == user.id:
                # ✅ CRITICAL: This is the function that is missing in repository.py
                return self.repo._to_entity_from_user_trade(trade_orm)
        
        return None
        
    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """
        Fetches most recent assets used by this user (Analyst Recs or User Trades).
        ✅ R2 Hotfix: Added Recommendation import to fix NameError.
        """
        logger.debug(f"TradeService (Facade) executing legacy 'get_recent_assets_for_user'")
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