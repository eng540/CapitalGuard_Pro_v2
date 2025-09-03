# --- START OF FILE: src/capitalguard/infrastructure/db/models/recommendation.py ---
from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, JSON, Text, Index, Enum
from datetime import datetime
from .base import Base
# ✅ --- Import the domain Enum to be used by SQLAlchemy ---
from capitalguard.domain.entities import RecommendationStatus

class RecommendationORM(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    asset = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)
    entry = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    targets = Column(JSON, nullable=False)
    
    # ✅ --- FIX: Change the column type to Enum to match the database schema ---
    status = Column(Enum(RecommendationStatus, name="recommendationstatus", create_type=False), 
                    default=RecommendationStatus.PENDING, 
                    index=True, 
                    nullable=False)

    # --- Publication Fields ---
    channel_id = Column(BigInteger, index=True, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)

    # --- User Experience Fields ---
    market = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    # --- Tracking Fields ---
    user_id = Column(String, nullable=True) # Changed to String in a previous migration
    exit_price = Column(Float, nullable=True)
    
    # ✅ --- FIX: Add the new lifecycle timestamp columns ---
    activated_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

Index("idx_recs_status_created", RecommendationORM.status, RecommendationORM.created_at.desc())
Index("idx_recs_asset_status",  RecommendationORM.asset,  RecommendationORM.status)
# --- END OF FILE ---