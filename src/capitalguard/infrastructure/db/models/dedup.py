from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from .base import Base


class DedupLedger(Base):
    """Durable idempotency ledger for forwarded/created trade signals."""

    __tablename__ = "dedup_ledger"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_channel_id",
            "fingerprint",
            "window_started_at",
            name="uq_dedup_user_channel_fingerprint_window",
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_channel_id = Column(BigInteger, nullable=True, index=True)
    fingerprint = Column(String(64), nullable=False, index=True)
    window_started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    outcome = Column(String(32), nullable=False, default="accepted")
    entity_type = Column(String(32), nullable=True)
    entity_id = Column(Integer, nullable=True)
    metadata_json = Column(String, nullable=True)
