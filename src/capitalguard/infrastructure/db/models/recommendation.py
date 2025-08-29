# --- START OF FILE: src/capitalguard/infrastructure/db/models/recommendation.py ---
from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, JSON, Text, Index
from datetime import datetime
from .base import Base

class RecommendationORM(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    asset = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)
    entry = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    targets = Column(JSON, nullable=False)
    status = Column(String, default="OPEN", nullable=False)

    # قناة/رسالة
    channel_id = Column(BigInteger, index=True, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    published_at = Column(DateTime, nullable=True)

    # إضافات UX
    market = Column(String, nullable=True)   # "Spot" | "Futures"
    notes = Column(Text, nullable=True)

    # تتبع
    user_id = Column(String, nullable=True)
    exit_price = Column(Float, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

Index("idx_recs_status_created", RecommendationORM.status, RecommendationORM.created_at.desc())
Index("idx_recs_asset_status",  RecommendationORM.asset,  RecommendationORM.status)
# --- END OF FILE ---