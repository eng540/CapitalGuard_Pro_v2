# --- START OF FILE: src/capitalguard/domain/entities.py ---
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum
from .value_objects import Symbol, Price, Targets, Side

class RecommendationStatus(Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"

# ✅ --- NEW: Define the core order types ---
class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"

@dataclass
class Recommendation:
    asset: Symbol
    side: Side
    entry: Price
    stop_loss: Price
    targets: Targets
    # ✅ --- ADDED: OrderType is now a fundamental part of a recommendation ---
    order_type: OrderType
    id: Optional[int] = None

    # --- Publication Fields ---
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

    def activate(self, activation_price: float) -> None:
        """Marks the recommendation as active and sets the official entry price for market orders."""
        if self.status == RecommendationStatus.PENDING:
            self.status = RecommendationStatus.ACTIVE
            self.updated_at = datetime.utcnow()
            self.activated_at = self.updated_at
            # For market orders, the activation price becomes the official entry price
            if self.order_type == OrderType.MARKET:
                self.entry = Price(activation_price)

    def close(self, exit_price: float) -> None:
        """Closes the recommendation with a given exit price."""
        if self.status == RecommendationStatus.CLOSED:
            return
        self.status = RecommendationStatus.CLOSED
        self.exit_price = exit_price
        self.updated_at = datetime.utcnow()
        self.closed_at = self.updated_at
# --- END OF FILE ---