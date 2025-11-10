# --- START OF NEW FILE: src/capitalguard/infrastructure/db/models/user_trade_event.py ---
# (R1-S1 Hotfix 10 - Bug B Fix)
"""
SQLAlchemy ORM Model for UserTradeEvent.
This table acts as an immutable audit log for events related to a UserTrade,
preventing duplicate notifications (Spam Bug B).
"""

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from .base import Base

class UserTradeEvent(Base):
    """
    Represents a single historical event in the lifecycle of a user's trade.
    This table acts as an immutable log to ensure stateful alerting.
    """
    __tablename__ = "user_trade_events"

    id = Column(Integer, primary_key=True)

    # Foreign key to the user_trade it belongs to
    user_trade_id = Column(Integer, ForeignKey("user_trades.id", ondelete="CASCADE"), nullable=False, index=True)

    # Type of the event, e.g., 'ACTIVATED', 'TP1_HIT', 'SL_HIT', 'CLOSED'
    event_type = Column(String(50), nullable=False, index=True)

    # Timestamp of when this event occurred
    event_timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # A flexible JSONB field to store data relevant to the event
    # Example for 'TP1_HIT': {"price": 123.45}
    event_data = Column(JSONB, nullable=True)

    # Defines the many-to-one relationship back to the UserTrade model
    user_trade = relationship("UserTrade", back_populates="events")

    def __repr__(self):
        return (
            f"<UserTradeEvent(id={self.id}, trade_id={self.user_trade_id}, "
            f"type='{self.event_type}')>"
        )
# --- END OF NEW FILE ---