from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .base import Base, JSON_TYPE


class HistoricalContentInterpretation(Base):
    """Deterministic, non-financial content understanding for one immutable revision."""

    __tablename__ = "historical_content_interpretations"
    __table_args__ = (
        UniqueConstraint("revision_id", "classifier_version", name="uq_hist_content_interpretation_revision_version"),
    )

    id = Column(Integer, primary_key=True)
    revision_id = Column(Integer, ForeignKey("historical_message_revisions.id", ondelete="CASCADE"), nullable=False, index=True)
    content_type = Column(String(40), nullable=False, index=True)
    confidence_score = Column(Numeric(5, 4), nullable=False)
    classifier_version = Column(String(40), nullable=False)
    classifier_method = Column(String(40), nullable=False, server_default="G2_DETERMINISTIC_RULES")
    meaning_json = Column(JSON_TYPE, nullable=True)
    provenance_json = Column(JSON_TYPE, nullable=False)
    ambiguity_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revision = relationship("HistoricalMessageRevision")
