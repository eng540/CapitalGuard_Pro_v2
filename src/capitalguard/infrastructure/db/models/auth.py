# src/capitalguard/infrastructure/db/models/auth.py (Updated for v3.0)

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum, BigInteger, func
from sqlalchemy.orm import relationship
from .base import Base

class UserType(enum.Enum):
    TRADER = 'TRADER'
    ANALYST = 'ANALYST'

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    user_type = Column(Enum(UserType), nullable=False, default=UserType.TRADER, server_default='TRADER')
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=False, server_default='false', nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    analyst_profile = relationship("AnalystProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    user_trades = relationship("UserTrade", back_populates="user", cascade="all, delete-orphan")
    subscriptions_as_trader = relationship("Subscription", foreign_keys="[Subscription.trader_user_id]", back_populates="trader")
    subscriptions_as_analyst = relationship("Subscription", foreign_keys="[Subscription.analyst_user_id]", back_populates="analyst")
    
    # Relationships to primary content created by this user (if they are an analyst)
    created_recommendations = relationship("Recommendation", back_populates="analyst")
    owned_channels = relationship("Channel", back_populates="analyst")