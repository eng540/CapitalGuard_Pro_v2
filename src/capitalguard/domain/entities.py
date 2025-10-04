# src/capitalguard/domain/entities.py (v3.0 - Final & Centralized)

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Any
from enum import Enum

from .value_objects import Symbol, Price, Targets, Side

class RecommendationStatus(Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"

class ExitStrategy(Enum):
    CLOSE_AT_FINAL_TP = "CLOSE_AT_FINAL_TP"
    MANUAL_CLOSE_ONLY = "MANUAL_CLOSE_ONLY"

@dataclass
class Recommendation:
    asset: Symbol
    side: Side
    entry: Price
    stop_loss: Price
    targets: Targets
    order_type: OrderType
    
    id: Optional[int] = None
    market: Optional[str] = "Futures"
    notes: Optional[str] = None
    user_id: Optional[str] = None # Analyst's Telegram ID

    status: RecommendationStatus = RecommendationStatus.PENDING
    exit_price: Optional[float] = None
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    activated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    exit_strategy: ExitStrategy = ExitStrategy.CLOSE_AT_FINAL_TP
    open_size_percent: float = 100.0
    
    events: Optional[List[Any]] = field(default=None, repr=False)

    def activate(self) -> None:
        if self.status == RecommendationStatus.PENDING:
            self.status = RecommendationStatus.ACTIVE
            self.updated_at = datetime.utcnow()
            self.activated_at = self.updated_at

    def close(self, exit_price: float) -> None:
        if self.status == RecommendationStatus.CLOSED:
            return
        self.status = RecommendationStatus.CLOSED
        self.exit_price = exit_price
        self.updated_at = datetime.utcnow()
        self.closed_at = self.updated_at