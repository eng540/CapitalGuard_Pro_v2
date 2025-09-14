# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Any
from enum import Enum
from .value_objects import Symbol, Price, Targets, Side

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

@dataclass
class Recommendation:
    """
    The core entity of the system, representing a single trade recommendation.
    It encapsulates all data and business logic related to a trade's lifecycle.
    """
    asset: Symbol
    side: Side
    entry: Price
    stop_loss: Price
    targets: Targets
    order_type: OrderType
    id: Optional[int] = None

    # --- Publication Fields (Legacy) ---
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    published_at: Optional[datetime] = None

    # --- User Experience Fields ---
    market: Optional[str] = "Futures"
    notes: Optional[str] = None
    user_id: Optional[str] = None

    # --- Lifecycle & Status Fields ---
    status: RecommendationStatus = RecommendationStatus.PENDING
    exit_price: Optional[float] = None

    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    activated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    # --- Metadata for alerts and UI ---
    # Example usage: {"hit_target_indices": [0, 1]}
    alert_meta: dict[str, Any] = field(default_factory=dict)

    # --- STRATEGY FIELDS ---
    exit_strategy: ExitStrategy = ExitStrategy.CLOSE_AT_FINAL_TP
    profit_stop_price: Optional[float] = None

    # --- PRICE TRACKING FIELDS ---
    highest_price_reached: Optional[float] = None
    lowest_price_reached: Optional[float] = None

    # --- PARTIAL PROFIT FIELD ---
    open_size_percent: float = 100.0
    
    # This relationship is populated by the repository, not part of the core domain
    events: Optional[List[Any]] = field(default=None, repr=False)


    def activate(self) -> None:
        """
        Marks the recommendation as active. This is called when the entry price is triggered.
        """
        if self.status == RecommendationStatus.PENDING:
            self.status = RecommendationStatus.ACTIVE
            self.updated_at = datetime.utcnow()
            self.activated_at = self.updated_at

    def close(self, exit_price: float) -> None:
        """Closes the recommendation with a given exit price."""
        if self.status == RecommendationStatus.CLOSED:
            return
        self.status = RecommendationStatus.CLOSED
        self.exit_price = exit_price
        self.updated_at = datetime.utcnow()
        self.closed_at = self.updated_at
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---