# --- START OF FILE: src/capitalguard/infrastructure/db/models/recommendation.py ---
import sqlalchemy as sa
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float,
    DateTime, JSON, Text, Index, Enum, func, ForeignKey
)
from sqlalchemy.orm import relationship # Import relationship
from sqlalchemy.dialects.postgresql import JSONB
from .base import Base
from capitalguard.domain.entities import RecommendationStatus, OrderType

class RecommendationORM(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    
    # This is the core change: user_id is now a foreign key to the users table.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

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

    channel_id = Column(BigInteger, index=True, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    market = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    exit_price = Column(Float, nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    alert_meta = Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    
    # Defines the relationship back to the User model
    user = relationship("User", back_populates="recommendations")

Index("idx_recs_status_created", RecommendationORM.status, RecommendationORM.created_at.desc())
Index("idx_recs_asset_status",  RecommendationORM.asset, RecommendationORM.status)
# --- END OF FILE ---