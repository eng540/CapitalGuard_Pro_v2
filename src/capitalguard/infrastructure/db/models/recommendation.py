# --- START OF FILE: src/capitalguard/infrastructure/db/models/recommendation.py ---
from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, JSON, Text, Index, Enum
from datetime import datetime
from .base import Base
# ✅ --- استيراد الـ Enum من النطاق ---
from capitalguard.domain.entities import RecommendationStatus

class RecommendationORM(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    asset = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)
    entry = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    targets = Column(JSON, nullable=False)
    
    # ✅ --- إصلاح: تغيير النوع إلى Enum ليتطابق مع قاعدة البيانات ---
    status = Column(Enum(RecommendationStatus, name="recommendationstatus", create_type=False), 
                    default=RecommendationStatus.PENDING, 
                    index=True, 
                    nullable=False)

    # --- حقول النشر في القناة ---
    channel_id = Column(BigInteger, index=True, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True) # تم إضافة timezone هنا للاتساق

    # --- حقول تجربة المستخدم ---
    market = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    # --- حقول التتبع ودورة الحياة ---
    user_id = Column(String, nullable=True)
    exit_price = Column(Float, nullable=True)
    
    # ✅ --- إصلاح: إضافة الأعمدة الجديدة لدورة الحياة ---
    activated_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # ✅ --- إصلاح: إضافة onupdate للتحديث التلقائي ---
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

Index("idx_recs_status_created", RecommendationORM.status, RecommendationORM.created_at.desc())
Index("idx_recs_asset_status",  RecommendationORM.asset,  RecommendationORM.status)
# --- END OF FILE ---