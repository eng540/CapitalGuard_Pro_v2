from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey, Index
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
    channel_id = Column(Integer, index=True, nullable=True)
    user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

Index("idx_recs_status_created", RecommendationORM.status, RecommendationORM.created_at.desc())
Index("idx_recs_asset_status", RecommendationORM.asset, RecommendationORM.status)
