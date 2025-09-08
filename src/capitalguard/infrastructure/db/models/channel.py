#// --- START: src/capitalguard/infrastructure/db/models/channel.py ---
from sqlalchemy import (
    Column, Integer, String, BigInteger, DateTime,
    ForeignKey, func, Boolean, Text
)
from sqlalchemy.orm import relationship
from .base import Base


class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True)

    # مالك القناة (مستخدم النظام)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # معرّف قناة تيليجرام (-100xxxxxxxxxx)، عادة رقم سالب وكبير
    telegram_channel_id = Column(BigInteger, unique=True, nullable=False, index=True)

    # اسم المستخدم للقناة العامة (بدون @). قد يكون None في القنوات الخاصة
    username = Column(String(255), unique=True, nullable=True)

    # الاسم المقروء للقناة (من getChat أو من الرسالة المُعادة التوجيه)
    title = Column(String(255), nullable=True)

    # حالة التفعيل للنشر (تعطيل/تفعيل سريع دون حذف الربط)
    is_active = Column(Boolean, default=True, nullable=False)

    # طوابع زمنية
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)

    # ملاحظات اختيارية داخلية
    notes = Column(Text, nullable=True)

    # علاقة عكسية للمستخدم
    user = relationship("User", back_populates="channels")

    def __repr__(self):
        return (
            f"<Channel(id={self.id}, tg_id={self.telegram_channel_id}, "
            f"username={repr(self.username)}, title={repr(self.title)}, user_id={self.user_id})>"
        )
#// --- END: src/capitalguard/infrastructure/db/models/channel.py ---