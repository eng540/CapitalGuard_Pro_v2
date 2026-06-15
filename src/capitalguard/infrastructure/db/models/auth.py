# src/capitalguard/infrastructure/db/models/auth.py (v25.1 - Enum Unification)
"""
SQLAlchemy ORM models for authentication and user management.
This version removes the redundant Enum definition and imports it from the domain,
establishing a single source of truth and fixing the permission bug.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum, BigInteger, func
from sqlalchemy.orm import relationship
from .base import Base

# ✅ THE DEFINITIVE FIX: Import the Enum from the single source of truth in the domain layer.
from capitalguard.domain.entities import UserType

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    # ✅ THE DEFINITIVE FIX: Use the imported Enum directly.
    user_type = Column(Enum(UserType), nullable=False, default=UserType.TRADER, server_default='TRADER')
    
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    
    is_active = Column(Boolean, default=False, server_default='false', nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # --- Relationships ---
    analyst_profile = relationship("AnalystProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    user_trades = relationship("UserTrade", back_populates="user", cascade="all, delete-orphan")
    
    # Relationships for analysts
    created_recommendations = relationship("Recommendation", back_populates="analyst")
    owned_channels = relationship("Channel", back_populates="analyst")

    def __repr__(self):
        return f"<User(id={self.id}, tg_id={self.telegram_user_id}, type='{self.user_type.value}', active={self.is_active})>"