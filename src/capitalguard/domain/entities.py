# --- START OF COMPLETE MODIFIED FILE: src/capitalguard/domain/entities.py ---
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
    # These are kept for temporary backward compatibility but the source of truth
    # is the `published_messages` table, accessed via the repository.
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

    # ✅ MODIFIED: Added a detailed docstring for the alert_meta field.
    alert_meta: dict = field(
        default_factory=dict,
        metadata={
            "description": (
                "A stateful JSON field to store metadata about alerts that have been triggered. "
                "This prevents duplicate notifications. Example keys:\n"
                " - 'trailing_applied': bool (True if SL has been moved to BE)\n"
                " - 'near_sl_alerted': bool (True if a near-SL private alert was sent)\n"
                " - 'near_tp1_alerted': bool (True if a near-TP1 private alert was sent)\n"
                " - 'tp1_hit_notified': bool (True if a public notification for TP1 hit was sent)\n"
                " - 'tp2_hit_notified': bool (etc. for all TPs)"
            )
        }
    )
    # --- END OF MODIFICATION ---

    def activate(self) -> None:
        """
        Marks the recommendation as active. This is called when the entry price is triggered.
        """
        if self.status == RecommendationStatus.PENDING:
            self.status = RecommendationStatus.ACTIVE
            self.updated_at = datetime.utcnow()
            self.activated_at = self.updated_at
            # For market orders, the activation happens at creation, so this method is
            # primarily for LIMIT/STOP_MARKET orders. The entry price is already set.

    def close(self, exit_price: float) -> None:
        """Closes the recommendation with a given exit price."""
        if self.status == RecommendationStatus.CLOSED:
            return
        self.status = RecommendationStatus.CLOSED
        self.exit_price = exit_price
        self.updated_at = datetime.utcnow()
        self.closed_at = self.updated_at
# --- END OF COMPLETE MODIFIED FILE ---