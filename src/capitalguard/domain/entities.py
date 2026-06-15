# -src/capitalguard/domain/entities.py (v25.0 - FINAL & UNIFIED)
"""
Defines the core business entities of the system. This is the heart of the domain layer.
✅ THE FIX (R1-S1): Expanded UserTradeStatus to include WATCHLIST and PENDING_ACTIVATION
       to support the new accounting logic (Trader-First) and channel auditing.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Any
from enum import Enum

from .value_objects import Symbol, Price, Targets, Side

# --- ENUMERATIONS ---

class RecommendationStatus(Enum):
    """Defines the possible lifecycle states of a recommendation."""
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"

class OrderType(Enum):
    """Defines the supported entry order types for a recommendation."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"

class ExitStrategy(Enum):
    """Defines the possible exit strategies for a recommendation."""
    CLOSE_AT_FINAL_TP = "CLOSE_AT_FINAL_TP"
    MANUAL_CLOSE_ONLY = "MANUAL_CLOSE_ONLY"

class UserTradeStatus(Enum):
    """
    Defines the state of a user's personal trade record.
    ✅ R1-S1: Updated for new logic.
    """
    WATCHLIST = "WATCHLIST" # Forwarded/Tracked for channel auditing. Does NOT count towards user PnL.
    PENDING_ACTIVATION = "PENDING_ACTIVATION" # User clicked "Activate", waiting for entry price hit. Does NOT count yet.
    ACTIVATED = "ACTIVATED" # Trade is live. This IS the basis for all user PnL calculations.
    CLOSED = "CLOSED" # Trade is closed.

class UserType(Enum):
    """Defines the roles a user can have within the system."""
    TRADER = "TRADER"
    ANALYST = "ANALYST"

# --- ENTITIES ---

@dataclass
class Recommendation:
    """
    The core entity representing a single trade recommendation from an analyst.
    It encapsulates all data and business logic related to a trade's lifecycle.
    """
    asset: Symbol
    side: Side
    entry: Price
    stop_loss: Price
    targets: Targets
    order_type: OrderType
    
    id: Optional[int] = None
    analyst_id: Optional[int] = None # Internal DB ID of the analyst user

    market: Optional[str] = "Futures"
    notes: Optional[str] = None
    
    status: RecommendationStatus = RecommendationStatus.PENDING
    exit_price: Optional[float] = None
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    activated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    exit_strategy: ExitStrategy = ExitStrategy.CLOSE_AT_FINAL_TP
    open_size_percent: float = 100.0
    
    is_shadow: bool = False

    # This relationship is populated by the repository, not part of the core domain.
    events: Optional[List[Any]] = field(default_factory=list, repr=False)

    def activate(self) -> None:
        """
        Marks the recommendation as active.
        Enforces the business rule that only
        PENDING recommendations can be activated.
        """
        if self.status == RecommendationStatus.PENDING:
            self.status = RecommendationStatus.ACTIVE
            self.updated_at = datetime.utcnow()
            self.activated_at = self.updated_at

    def close(self, exit_price: float) -> None:
        """
        Closes the recommendation.
        This method is idempotent.
        """
        if self.status == RecommendationStatus.CLOSED:
            return
        self.status = RecommendationStatus.CLOSED
        self.exit_price = exit_price
        self.updated_at = datetime.utcnow()
        self.closed_at = self.updated_at

@dataclass
class UserTrade:
    """
    Entity representing a user's personal trade, which may be linked to an
    official recommendation or be a standalone tracked trade.
    """
    id: int
    user_id: int
    asset: Symbol
    side: Side
    entry: Price
    stop_loss: Price
    targets: Targets
    status: UserTradeStatus # ✅ R1-S1: Uses the expanded Enum
    
    source_recommendation_id: Optional[int] = None
    close_price: Optional[float] = None
    pnl_percentage: Optional[float] = None
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    
    # ✅ R1-S1: Add new fields for auditing
    original_published_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    watched_channel_id: Optional[int] = None