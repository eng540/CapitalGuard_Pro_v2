from sqlalchemy import BigInteger, Column, DateTime, Integer, Numeric, String, UniqueConstraint, func

from .base import Base, JSON_TYPE


class TemporalForwardDecision(Base):
    """Auditable decision snapshot for one Telegram receiver message."""

    __tablename__ = "temporal_forward_decisions"
    __table_args__ = (
        UniqueConstraint(
            "receiver_chat_id",
            "receiver_message_id",
            name="uq_temporal_forward_receiver_message",
        ),
    )

    id = Column(Integer, primary_key=True)
    receiver_chat_id = Column(BigInteger, nullable=False, index=True)
    receiver_message_id = Column(BigInteger, nullable=False, index=True)
    source_chat_id = Column(BigInteger, nullable=True, index=True)
    source_message_id = Column(BigInteger, nullable=True, index=True)
    source_message_revision = Column(Integer, nullable=False, server_default="0")
    source_time = Column(DateTime(timezone=True), nullable=True, index=True)
    event_time = Column(DateTime(timezone=True), nullable=True, index=True)
    received_time = Column(DateTime(timezone=True), nullable=False, index=True)
    ingested_time = Column(DateTime(timezone=True), nullable=False, index=True)
    edit_time = Column(DateTime(timezone=True), nullable=True)
    mode = Column(String(40), nullable=False, index=True)
    route = Column(String(40), nullable=False, index=True)
    timeline_relation = Column(String(40), nullable=False, index=True)
    confidence = Column(Numeric(5, 4), nullable=False)
    price_validity_score = Column(Numeric(5, 4), nullable=True)
    age_seconds = Column(Integer, nullable=True)
    replay_readiness = Column(Numeric(5, 4), nullable=False)
    reason_codes = Column(JSON_TYPE, nullable=False)
    metadata_json = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
