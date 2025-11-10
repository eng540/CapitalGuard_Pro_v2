# src/capitalguard/infrastructure/db/models/watched_channel.py (NEW FILE R1-S1)
"""
SQLAlchemy ORM model for Watched Channels.
This table links a User to a Telegram Channel they are auditing via Smart Forwarding.
"""

import sqlalchemy as sa
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean,
    ForeignKey, BigInteger, func, UniqueConstraint
)
from sqlalchemy.orm import relationship
from .base import Base

class WatchedChannel(Base):
    __tablename__ = 'watched_channels'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # The user who is doing the watching
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    
    # The Telegram Channel ID being watched (e.g., -100123456789)
    telegram_channel_id = Column(BigInteger, nullable=False, index=True)
    
    # The title of the channel (captured on first forward for easy display)
    channel_title = Column(String(255), nullable=True)
    
    # Is the user actively watching this channel? (allows user to "unwatch")
    is_active = Column(Boolean, default=True, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # A user can watch many channels, but can only watch the SAME channel ONCE.
    __table_args__ = (
        UniqueConstraint('user_id', 'telegram_channel_id', name='uq_user_channel_watch'),
    )

    # Relationships
    user = relationship("User")
    
    # One WatchedChannel (by a specific user) can have many UserTrades sourced from it
    user_trades = relationship("UserTrade", back_populates="watched_channel")

    def __repr__(self):
        return f"<WatchedChannel(id={self.id}, user_id={self.user_id}, tg_channel_id={self.telegram_channel_id}, title='{self.channel_title}')>"