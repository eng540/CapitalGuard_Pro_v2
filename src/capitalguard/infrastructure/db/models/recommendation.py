# --- START OF FILE: src/capitalguard/infrastructure/db/models/recommendation.py ---
from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, JSON, Text, Index, Enum
from datetime import datetime
from .base import Base
# ✅ --- Import both Enums ---
from capitalguard.domain.entities import RecommendationStatus, OrderType

class RecommendationORM(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    asset = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)
    entry = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    targets = Column(JSON, nullable=False)
    
    # ✅ --- ADDED: New order_type column with its own Enum ---
    order_type = Column(Enum(OrderType, name="ordertype", create_type=False),
                        default=OrderType.LIMIT,
                        nullable=False)
    
    status = Column(Enum(RecommendationStatus, name="recommendationstatus", create_type=False), 
                    default=RecommendationStatus.PENDING, 
                    index=True, 
                    nullable=False)

    # --- (Other fields remain the same) ---
    channel_id = Column(BigInteger, index=True, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    market = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    user_id = Column(String, nullable=True)
    exit_price = Column(Float, nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

Index("idx_recs_status_created", RecommendationORM.status, RecommendationORM.created_at.desc())
Index("idx_recs_asset_status",  RecommendationORM.asset,  RecommendationORM.status)
# --- END OF FILE ---