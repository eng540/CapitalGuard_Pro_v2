# src/capitalguard/infrastructure/db/models/recommendation.py (v25.2 - Forwarding Fix)
"""
SQLAlchemy ORM models related to recommendations, user trades, and their lifecycle.
✅ FIX: Added the missing 'source_forwarded_text' column to the UserTrade model
to align it with the database schema and fix the TypeError on trade creation.
"""

from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean,
    ForeignKey, Enum, Text, BigInteger, Numeric, func
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from .base import Base

from capitalguard.domain.entities import (
    RecommendationStatus as RecommendationStatusEnum,
    OrderType as OrderTypeEnum,
    ExitStrategy as ExitStrategyEnum,
    UserTradeStatus
)


# --- TABLES ---

class AnalystProfile(Base):
    __tablename__ = 'analyst_profiles'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False, unique=True)
    public_name = Column(String, nullable=True)
    bio = Column(Text, nullable=True)
    is_public = Column(Boolean, default=False, server_default='false', nullable=False)
    
    user = relationship("User", back_populates="analyst_profile")
    stats = relationship("AnalystStats", back_populates="analyst_profile", uselist=False, cascade="all, delete-orphan")

class Channel(Base):
    __tablename__ = 'channels'
    id = Column(Integer, primary_key=True)
    analyst_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    telegram_channel_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String, nullable=True)
    title = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    analyst = relationship("User", back_populates="owned_channels")
    recommendations = relationship("Recommendation", back_populates="channel")

class Recommendation(Base):
    __tablename__ = 'recommendations'
    id = Column(Integer, primary_key=True)
    analyst_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    channel_id = Column(Integer, ForeignKey('channels.id'), nullable=True)
    
    asset = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    entry = Column(Numeric(20, 8), nullable=False)
    stop_loss = Column(Numeric(20, 8), nullable=False)
    targets = Column(JSONB, nullable=False)
    
    status = Column(Enum(RecommendationStatusEnum, name="recommendationstatusenum"), nullable=False, default=RecommendationStatusEnum.PENDING, index=True)
    order_type = Column(Enum(OrderTypeEnum, name="ordertypeenum"), nullable=False, default=OrderTypeEnum.LIMIT)
    exit_strategy = Column(Enum(ExitStrategyEnum, name="exitstrategyenum"), nullable=False, default=ExitStrategyEnum.CLOSE_AT_FINAL_TP)
    
    market = Column(String, nullable=True, default="Futures")
    notes = Column(Text, nullable=True)
    
    open_size_percent = Column(Numeric(5, 2), nullable=False, server_default='100.00')
    exit_price = Column(Numeric(20, 8), nullable=True)
    is_shadow = Column(Boolean, default=False, server_default='false', nullable=False, index=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    analyst = relationship("User", back_populates="created_recommendations")
    channel = relationship("Channel", back_populates="recommendations")
    events = relationship("RecommendationEvent", back_populates="recommendation", cascade="all, delete-orphan", lazy="selectin")
    user_trades = relationship("UserTrade", back_populates="source_recommendation") # Removed cascade
    published_messages = relationship("PublishedMessage", back_populates="recommendation", cascade="all, delete-orphan")

class UserTrade(Base):
    __tablename__ = 'user_trades'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    
    source_recommendation_id = Column(Integer, ForeignKey('recommendations.id', ondelete="SET NULL"), nullable=True, index=True)
    
    asset = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    entry = Column(Numeric(20, 8), nullable=False)
    stop_loss = Column(Numeric(20, 8), nullable=False)
    targets = Column(JSONB, nullable=False)
    status = Column(Enum(UserTradeStatus, name="usertradestatus"), nullable=False, default=UserTradeStatus.OPEN, index=True)
    
    close_price = Column(Numeric(20, 8), nullable=True)
    pnl_percentage = Column(Numeric(10, 4), nullable=True)
    
    # ✅ THE FIX: Added the missing column definition to match the DB schema.
    source_forwarded_text = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="user_trades")
    source_recommendation = relationship("Recommendation", back_populates="user_trades")

class RecommendationEvent(Base):
    __tablename__ = 'recommendation_events'
    id = Column(Integer, primary_key=True)
    recommendation_id = Column(Integer, ForeignKey('recommendations.id', ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    event_data = Column(JSONB, nullable=True)
    event_timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    recommendation = relationship("Recommendation", back_populates="events")

class Subscription(Base):
    __tablename__ = 'subscriptions'
    id = Column(Integer, primary_key=True)
    trader_user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False)
    analyst_user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False)
    start_date = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

class AnalystStats(Base):
    __tablename__ = 'analyst_stats'
    analyst_profile_id = Column(Integer, ForeignKey('analyst_profiles.id', ondelete="CASCADE"), primary_key=True)
    win_rate = Column(Numeric(5, 2), nullable=True)
    total_pnl = Column(Numeric(10, 4), nullable=True)
    total_trades = Column(Integer, default=0)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    analyst_profile = relationship("AnalystProfile", back_populates="stats")

class PublishedMessage(Base):
    __tablename__ = 'published_messages'
    id = Column(Integer, primary_key=True)
    recommendation_id = Column(Integer, ForeignKey('recommendations.id', ondelete="CASCADE"), nullable=False, index=True)
    telegram_channel_id = Column(BigInteger, nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)

    recommendation = relationship("Recommendation", back_populates="published_messages")