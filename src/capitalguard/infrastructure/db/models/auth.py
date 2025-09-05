#--- START OF FILE: src/capitalguard/infrastructure/db/models/auth.py ---
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, UniqueConstraint, BigInteger, DateTime, func
from sqlalchemy.orm import relationship
from .base import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False)
    user_type = Column(String(50), nullable=False, default='trader') # 'trader' or 'analyst'
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    recommendations = relationship("RecommendationORM", back_populates="user")

class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False) # e.g., 'admin', 'premium_trader'

class UserRole(Base):
    __tablename__ = "user_roles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    
    user = relationship("User", back_populates="roles")
    role = relationship("Role")
    
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)
#--- END OF FILE ---