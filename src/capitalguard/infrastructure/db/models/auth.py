#// --- START: src/capitalguard/infrastructure/db/models/auth.py ---
from sqlalchemy import (
    Column, Integer, String, BigInteger, DateTime, 
    ForeignKey, UniqueConstraint, func, Boolean
)
from sqlalchemy.orm import relationship
from .base import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    
    # --- ✅ Matching the actual database schema ---
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True) # Password can be null for TG-only users
    is_active = Column(Boolean, default=True, nullable=False)
    # --- ✅ End of matching ---

    telegram_user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    user_type = Column(String(50), nullable=False, default='trader', server_default='trader')
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    recommendations = relationship("RecommendationORM", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}', telegram_id={self.telegram_user_id})>"

class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)

class UserRole(Base):
    __tablename__ = "user_roles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    
    user = relationship("User", back_populates="roles")
    role = relationship("Role")
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)
#// --- END: src/capitalguard/infrastructure/db/models/auth.py ---