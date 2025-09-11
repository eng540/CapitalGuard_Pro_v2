# --- START OF FINAL, CORRECTED FILE (V8): src/capitalguard/domain/entities.py ---
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
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

    alert_meta: dict = field(default_factory=dict)

    # ✅ --- START: FIX for TypeError ---
    # Add the new tracking fields to the domain entity definition.
    highest_price_reached: Optional[float] = None
    lowest_price_reached: Optional[float] = None
    # ✅ --- END: FIX for TypeError ---

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
# --- END OF FINAL, CORRECTED FILE (V8) ---