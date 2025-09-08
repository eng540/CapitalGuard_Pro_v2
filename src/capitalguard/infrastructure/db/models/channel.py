#// --- START: src/capitalguard/infrastructure/db/models/channel.py ---
from sqlalchemy import (
    Column, Integer, String, BigInteger, DateTime,
    ForeignKey, func, Boolean
)
from sqlalchemy.orm import relationship
from .base import Base


class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True)

    # مالك القناة (مستخدم النظام)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # معرّف قناة التيليجرام (عدد صحيح كبير وفريد)
    telegram_channel_id = Column(BigInteger, unique=True, nullable=False, index=True)

    # اسم المستخدم للقناة (username) مثل @MySignalChannel — نخزّنه بلا @ وبشكل فريد
    username = Column(String, unique=True, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # علاقة عكسية للمستخدم
    user = relationship("User", back_populates="channels")

    def __repr__(self):
        return f"<Channel(id={self.id}, username='{self.username}', user_id={self.user_id})>"
#// --- END: src/capitalguard/infrastructure/db/models/channel.py ---