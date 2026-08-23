from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .base import Base, JSON_TYPE


class HistoricalSignalMaterialization(Base):
    """Immutable G5 bridge from one accepted G4 draft to one historical signal."""

    __tablename__ = "historical_signal_materializations"
    __table_args__ = (
        UniqueConstraint("draft_id", name="uq_hist_signal_materialization_draft"),
        UniqueConstraint("signal_id", name="uq_hist_signal_materialization_signal"),
    )

    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer, ForeignKey("historical_recommendation_drafts.id", ondelete="RESTRICT"), nullable=False, index=True)
    signal_id = Column(Integer, ForeignKey("historical_signals.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision_id = Column(Integer, ForeignKey("historical_message_revisions.id", ondelete="RESTRICT"), nullable=False, index=True)
    evidence_id = Column(Integer, ForeignKey("historical_signal_evidence.id", ondelete="RESTRICT"), nullable=False, index=True)
    materialization_kind = Column(String(40), nullable=False, index=True)
    source_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    provenance_json = Column(JSON_TYPE, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    draft = relationship("HistoricalRecommendationDraft")
    signal = relationship("HistoricalSignal")
    revision = relationship("HistoricalMessageRevision")
    evidence = relationship("HistoricalSignalEvidence")
