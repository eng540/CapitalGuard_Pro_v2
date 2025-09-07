from sqlalchemy import (
    Column, Integer, String, BigInteger, DateTime,
    ForeignKey, UniqueConstraint, Boolean, func
)
from sqlalchemy.orm import relationship

from .base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    # ✅ مطابق لِمخطط Railway
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)  # تيليجرام فقط = قد يكون None
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    telegram_user_id = Column(BigInteger, unique=True, index=True, nullable=False)
    user_type = Column(String(50), nullable=False, default="trader", server_default="trader")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # علاقات
    recommendations = relationship(
        "RecommendationORM",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    roles = relationship(
        "UserRole",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tg={self.telegram_user_id} email='{self.email}'>"


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Role id={self.id} name='{self.name}'>"


class UserRole(Base):
    __tablename__ = "user_roles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)

    user = relationship("User", back_populates="roles")
    role = relationship("Role")

    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role"),
    )

    def __repr__(self) -> str:
        return f"<UserRole user_id={self.user_id} role_id={self.role_id}>"