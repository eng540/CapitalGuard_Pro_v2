"""Canonical identity for Telegram channels across ownership and watch relations."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, func
from sqlalchemy.orm import relationship

from .base import Base


class ChannelCatalog(Base):
    __tablename__ = "channel_catalog"

    id = Column(Integer, primary_key=True, autoincrement=True)
    public_ref = Column(String(40), unique=True, nullable=True, index=True)
    channel_code = Column(String(20), unique=True, nullable=True, index=True)
    telegram_channel_id = Column(BigInteger, unique=True, nullable=False, index=True)
    title = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    recommendation_refs = relationship("RecommendationChannelRef", back_populates="channel_catalog")
