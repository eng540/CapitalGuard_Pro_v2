# --- START OF FILE: src/capitalguard/domain/entities.py ---
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from .value_objects import Symbol, Price, Targets, Side

@dataclass
class Recommendation:
    asset: Symbol
    side: Side
    entry: Price
    stop_loss: Price
    targets: Targets
    id: Optional[int] = None
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    published_at: Optional[datetime] = None
    user_id: Optional[str] = None
    status: str = "OPEN"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    exit_price: Optional[float] = None
    closed_at: Optional[datetime] = None

    def close(self, exit_price: float) -> None:
        if self.status == "CLOSED":
            return
        self.status = "CLOSED"
        self.updated_at = datetime.utcnow()
        self.exit_price = exit_price
        self.closed_at = self.updated_at
# --- END OF FILE ---