from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .base import Base, JSON_TYPE


class HistoricalCanonicalMessage(Base):
    """Stable source-scoped identity for a historical or future live message.

    This foundation owns message identity only. It must not create a live
    Recommendation, UserTrade, outbox item, market event, or replay request.
    """

    __tablename__ = "historical_canonical_messages"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_chat_id", "external_message_id", name="uq_hist_canonical_source_message"),
    )

    id = Column(Integer, primary_key=True)
    source_kind = Column(String(32), nullable=False, index=True)
    source_chat_id = Column(BigInteger, nullable=False, index=True)
    external_message_id = Column(BigInteger, nullable=False, index=True)
    ingestion_mode = Column(String(24), nullable=False, server_default="HISTORICAL", index=True)
    first_observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    latest_revision_number = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revisions = relationship("HistoricalMessageRevision", back_populates="message", cascade="all, delete-orphan")


class HistoricalMessageRevision(Base):
    """Immutable observed content revision for a canonical message."""

    __tablename__ = "historical_message_revisions"
    __table_args__ = (
        UniqueConstraint("message_id", "revision_number", name="uq_hist_message_revision_number"),
        UniqueConstraint("message_id", "content_hash", name="uq_hist_message_revision_hash"),
    )

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("historical_canonical_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    source_timestamp = Column(DateTime(timezone=True), nullable=True, index=True)
    source_edit_date = Column(DateTime(timezone=True), nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    raw_text = Column(Text, nullable=True)
    source_origin_type = Column(String(40), nullable=False)
    source_reply_to_message_id = Column(BigInteger, nullable=True, index=True)
    safe_classification = Column(String(32), nullable=False, server_default="UNKNOWN", index=True)
    classification_confidence = Column(Numeric(5, 4), nullable=False, server_default="0")
    classification_method = Column(String(40), nullable=False, server_default="G1_SAFE_RULES")
    receipt_id = Column(Integer, ForeignKey("historical_forward_receipts.id", ondelete="SET NULL"), nullable=True, index=True)
    evidence_id = Column(Integer, ForeignKey("historical_signal_evidence.id", ondelete="SET NULL"), nullable=True, index=True)
    provenance_json = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    message = relationship("HistoricalCanonicalMessage", back_populates="revisions")


class HistoricalMessageRelationship(Base):
    """A reviewable, non-financial proposed relationship between source messages."""

    __tablename__ = "historical_message_relationships"
    __table_args__ = (
        UniqueConstraint("source_message_id", "target_message_id", "relationship_type", "method", name="uq_hist_message_relationship"),
    )

    id = Column(Integer, primary_key=True)
    source_message_id = Column(Integer, ForeignKey("historical_canonical_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    target_message_id = Column(Integer, ForeignKey("historical_canonical_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type = Column(String(40), nullable=False, index=True)
    confidence_score = Column(Numeric(5, 4), nullable=False, server_default="0")
    method = Column(String(40), nullable=False, server_default="G1_SAFE_RULES")
    evidence_json = Column(JSON_TYPE, nullable=True)
    review_status = Column(String(24), nullable=False, server_default="PENDING", index=True)
    reviewed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

