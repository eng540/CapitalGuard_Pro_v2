"""
Channel model — final and production-ready.
Aligned with Alembic baseline schema (20251007_v3_baseline).
"""

from sqlalchemy import (
    Column, Integer, String, BigInteger, DateTime,
    ForeignKey, func, Boolean, Text
)
from sqlalchemy.orm import relationship
from .base import Base


class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True)

    # المحلل (مالك القناة)
    analyst_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # معرف القناة في تيليجرام (يبدأ عادة بـ -100)
    telegram_channel_id = Column(BigInteger, unique=True, nullable=False, index=True)

    # اسم المستخدم للقناة العامة بدون @ (قد يكون None للقنوات الخاصة)
    username = Column(String(255), unique=True, nullable=True)

    # الاسم المقروء للقناة
    title = Column(String(255), nullable=True)

    # حالة التفعيل للنشر
    is_active = Column(Boolean, default=True, nullable=False)

    # الطوابع الزمنية
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)

    # ملاحظات داخلية اختيارية
    notes = Column(Text, nullable=True)

    # العلاقة مع المستخدم (المحلل)
    analyst = relationship("User", back_populates="channels")

    def __repr__(self):
        return (
            f"<Channel(id={self.id}, tg_id={self.telegram_channel_id}, "
            f"username={repr(self.username)}, title={repr(self.title)}, analyst_id={self.analyst_id})>"
        )