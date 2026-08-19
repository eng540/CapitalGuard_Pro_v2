"""Non-commercial entitlement and subscription ledger models for R3."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from .base import Base, JSON_TYPE


class EntitlementGrant(Base):
    __tablename__ = "entitlement_grants"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_code = Column(String(80), nullable=False, index=True)
    source = Column(String(32), nullable=False, server_default="ALPHA_GRANT")
    status = Column(String(20), nullable=False, server_default="GRANTED", index=True)
    starts_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    idempotency_key = Column(String(160), nullable=False, unique=True)
    metadata_json = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])


class SubscriptionLedgerEntry(Base):
    __tablename__ = "subscription_ledger_entries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    entry_type = Column(String(32), nullable=False, index=True)
    plan_code = Column(String(80), nullable=True, index=True)
    feature_code = Column(String(80), nullable=True, index=True)
    amount_minor = Column(Integer, nullable=False, server_default="0")
    currency = Column(String(3), nullable=False, server_default="USD")
    provider = Column(String(32), nullable=False, server_default="INTERNAL")
    provider_event_id = Column(String(160), nullable=True, unique=True)
    status = Column(String(20), nullable=False, server_default="RECORDED", index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    idempotency_key = Column(String(160), nullable=False, unique=True)
    metadata_json = Column(JSON_TYPE, nullable=True)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])
