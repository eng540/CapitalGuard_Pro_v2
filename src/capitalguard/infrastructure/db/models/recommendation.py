# --- START OF FILE: src/capitalguard/infrastructure/db/models/recommendation.py ---
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float,
    DateTime, JSON, Text, Index, Enum, func
)
from .base import Base
from capitalguard.domain.entities import RecommendationStatus, OrderType

class RecommendationORM(Base):
    """
    SQLAlchemy ORM model for the 'recommendations' table.
    Mirrors the database structure, including Enums for status and order_type.
    """
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    asset = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)
    entry = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    targets = Column(JSON, nullable=False)
    
    # --- Enum column for order_type ---
    order_type = Column(
        Enum(OrderType, name="ordertype", create_type=False),
        default=OrderType.LIMIT,
        nullable=False
    )
    
    # --- Enum column for status ---
    status = Column(
        Enum(RecommendationStatus, name="recommendationstatus", create_type=False),
        default=RecommendationStatus.PENDING,
        index=True,
        nullable=False
    )

    # --- Publication Fields ---
    channel_id = Column(BigInteger, index=True, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)

    # --- User Experience Fields ---
    market = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    # --- Tracking & Lifecycle Fields ---
    user_id = Column(String, nullable=True)
    exit_price = Column(Float, nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

# --- Indexes for performance ---
Index("idx_recs_status_created", RecommendationORM.status, RecommendationORM.created_at.desc())
Index("idx_recs_asset_status",  RecommendationORM.asset, RecommendationORM.status)
# --- END OF FILE ---