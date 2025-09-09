# --- START OF NEW FILE: src/capitalguard/infrastructure/db/models/published_message.py ---
from sqlalchemy import (
    Column, Integer, BigInteger, DateTime,
    ForeignKey, func
)
from sqlalchemy.orm import relationship
from .base import Base

class PublishedMessage(Base):
    __tablename__ = "published_messages"

    id = Column(Integer, primary_key=True)
    
    # Foreign key to the recommendation it belongs to
    recommendation_id = Column(Integer, ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Telegram-specific identifiers
    telegram_channel_id = Column(BigInteger, nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)
    
    # Timestamp
    published_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationship back to the RecommendationORM
    recommendation = relationship("RecommendationORM", back_populates="published_messages")

    def __repr__(self):
        return (
            f"<PublishedMessage(id={self.id}, rec_id={self.recommendation_id}, "
            f"channel_id={self.telegram_channel_id}, msg_id={self.telegram_message_id})>"
        )
# --- END OF NEW FILE ---