from enum import Enum

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .base import Base


class PublicationDeliveryStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    RETRY = "RETRY"
    FAILED = "FAILED"


class PublicationDeliveryOperation(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    REPLY = "REPLY"
    CLOSE = "CLOSE"


class PublicationDelivery(Base):
    __tablename__ = "publication_deliveries"

    id = Column(Integer, primary_key=True)
    recommendation_id = Column(
        Integer,
        ForeignKey("recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_channel_id = Column(BigInteger, nullable=False)
    operation = Column(String(16), nullable=False, default=PublicationDeliveryOperation.CREATE.value)
    status = Column(String(16), nullable=False, default=PublicationDeliveryStatus.PENDING.value, index=True)
    idempotency_key = Column(String(255), nullable=False, unique=True)
    telegram_message_id = Column(BigInteger, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)

    recommendation = relationship("Recommendation", back_populates="publication_deliveries")

    __table_args__ = (
        UniqueConstraint(
            "recommendation_id",
            "telegram_channel_id",
            "operation",
            name="uq_publication_delivery_target_operation",
        ),
        Index(
            "ix_publication_deliveries_retry_queue",
            "status",
            "next_attempt_at",
        ),
    )
