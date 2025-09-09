# --- START OF COMPLETE MODIFIED FILE: src/capitalguard/infrastructure/db/models/recommendation.py ---
import sqlalchemy as sa
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float,
    DateTime, JSON, Text, Index, Enum, func, ForeignKey
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from .base import Base
from capitalguard.domain.entities import RecommendationStatus, OrderType

class RecommendationORM(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    
    # --- ✅ ARCHITECTURAL LEAP: user_id is now a mandatory foreign key ---
    # Every recommendation MUST belong to a user.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    asset = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)
    entry = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    targets = Column(JSON, nullable=False)
    
    order_type = Column(
        Enum(OrderType, name="ordertype", create_type=False),
        default=OrderType.LIMIT, nullable=False
    )
    status = Column(
        Enum(RecommendationStatus, name="recommendationstatus", create_type=False),
        default=RecommendationStatus.PENDING, index=True, nullable=False
    )

    # --- LEGACY FIELDS (to be deprecated) ---
    # These fields are kept for now for backward compatibility during the transition,
    # but new logic should use the `published_messages` relationship.
    channel_id = Column(BigInteger, index=True, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    # --- END LEGACY FIELDS ---

    published_at = Column(DateTime(timezone=True), nullable=True)
    market = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    exit_price = Column(Float, nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    alert_meta = Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    
    # Defines the relationship back to the User model
    user = relationship("User", back_populates="recommendations")

    # ✅ --- NEW RELATIONSHIP to PublishedMessage ---
    # Defines the one-to-many relationship. When a RecommendationORM is loaded,
    # its associated PublishedMessage objects will also be loaded efficiently.
    published_messages = relationship(
        "PublishedMessage", 
        back_populates="recommendation", 
        cascade="all, delete-orphan",
        lazy="joined" # Use joined loading for performance optimization
    )
    # --- END NEW RELATIONSHIP ---

    def __repr__(self):
        return f"<RecommendationORM(id={self.id}, user_id={self.user_id}, asset='{self.asset}')>"

# Existing indexes are good, no changes needed here.
Index("idx_recs_status_created", RecommendationORM.status, RecommendationORM.created_at.desc())
Index("idx_recs_asset_status",  RecommendationORM.asset, RecommendationORM.status)
# --- END OF COMPLETE MODIFIED FILE ---