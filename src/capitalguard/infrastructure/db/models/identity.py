"""Persistent counters for collision-safe scoped identity allocation."""
from sqlalchemy import Column, Integer, String, UniqueConstraint

from .base import Base


class ScopedIdentityCounter(Base):
    __tablename__ = "scoped_identity_counters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_type = Column(String(40), nullable=False)
    scope_id = Column(Integer, nullable=False, default=0)
    next_value = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", name="uq_identity_counter_scope"),
    )
