# --- START OF NEW FILE: src/capitalguard/infrastructure/db/models/recommendation_event.py ---
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from .base import Base

class RecommendationEvent(Base):
    """
    Represents a single historical event in the lifecycle of a recommendation.
    This table acts as an immutable log.
    """
    __tablename__ = "recommendation_events"

    id = Column(Integer, primary_key=True)
    
    # Foreign key to the recommendation it belongs to
    recommendation_id = Column(Integer, ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Type of the event, e.g., 'CREATE', 'SL_UPDATE', 'TP_UPDATE', 'ACTIVATED', 'CLOSED'
    event_type = Column(String(50), nullable=False, index=True)
    
    # Timestamp of when this event occurred
    event_timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # A flexible JSONB field to store data relevant to the event
    # Example for 'SL_UPDATE': {"old_sl": 50000, "new_sl": 51000}
    event_data = Column(JSONB, nullable=True)

    # Defines the many-to-one relationship back to the RecommendationORM model
    recommendation = relationship("RecommendationORM", back_populates="events")

    def __repr__(self):
        return (
            f"<RecommendationEvent(id={self.id}, rec_id={self.recommendation_id}, "
            f"type='{self.event_type}')>"
        )
# --- END OF NEW FILE ---