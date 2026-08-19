from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .base import Base, JSON_TYPE


class HistoricalForwardReceipt(Base):
    """Audit receipt for a message forwarded to the historical staging intake."""

    __tablename__ = "historical_forward_receipts"
    __table_args__ = (
        UniqueConstraint("receiver_chat_id", "receiver_message_id", name="uq_hist_forward_receiver_message"),
        UniqueConstraint(
            "source_chat_id",
            "source_message_id",
            "source_message_revision",
            name="uq_hist_forward_source_revision",
        ),
    )

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("historical_import_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id = Column(Integer, ForeignKey("historical_signal_evidence.id", ondelete="SET NULL"), nullable=True, index=True)
    forwarding_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    receiver_chat_id = Column(BigInteger, nullable=False, index=True)
    receiver_message_id = Column(BigInteger, nullable=False, index=True)
    source_chat_id = Column(BigInteger, nullable=True, index=True)
    source_message_id = Column(BigInteger, nullable=True, index=True)
    source_message_revision = Column(Integer, nullable=False, server_default="0")
    source_origin_type = Column(String(40), nullable=False)
    source_message_timestamp = Column(DateTime(timezone=True), nullable=True, index=True)
    source_edit_date = Column(DateTime(timezone=True), nullable=True)
    source_reply_to_message_id = Column(BigInteger, nullable=True)
    raw_text = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    validation_status = Column(String(24), nullable=False, server_default="STAGED", index=True)
    rejection_reason = Column(String(255), nullable=True)
    metadata_json = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    batch = relationship("HistoricalImportBatch")
    evidence = relationship("HistoricalSignalEvidence")
    forwarding_user = relationship("User")
