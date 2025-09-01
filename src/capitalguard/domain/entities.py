#--- START OF FILE: src/capitalguard/domain/entities.py ---
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from .value_objects import Symbol, Price, Targets, Side

@dataclass
class Recommendation:
    asset: Symbol
    side: Side
    entry: Price
    stop_loss: Price
    targets: Targets
    id: Optional[int] = None

    # الحقول المتعلقة بالنشر في قناة تليجرام
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    published_at: Optional[datetime] = None

    # حقول إضافية لتجربة المستخدم
    market: Optional[str] = "Futures"
    notes: Optional[str] = None
    user_id: Optional[str] = None

    # حقول تتبع الحالة والإغلاق
    status: str = "OPEN"
    exit_price: Optional[float] = None
    closed_at: Optional[datetime] = None
    
    # حقول التوقيت
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def close(self, exit_price: float) -> None:
        if self.status == "CLOSED":
            return
        self.status = "CLOSED"
        self.exit_price = exit_price
        self.updated_at = datetime.utcnow()
        self.closed_at = self.updated_at
#--- END OF FILE ---