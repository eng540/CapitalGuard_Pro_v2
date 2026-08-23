from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship
from .base import Base, JSON_TYPE


class HistoricalFinancialCandidate(Base):
    __tablename__ = "historical_financial_candidates"
    __table_args__ = (UniqueConstraint("interpretation_id", "field_type", "normalized_value", "span_text", "extractor_version", name="uq_hist_financial_candidate"),)
    id = Column(Integer, primary_key=True)
    interpretation_id = Column(Integer, ForeignKey("historical_content_interpretations.id", ondelete="CASCADE"), nullable=False, index=True)
    field_type = Column(String(32), nullable=False, index=True)
    value_json = Column(JSON_TYPE, nullable=False)
    normalized_value = Column(String(160), nullable=False, index=True)
    span_text = Column(Text, nullable=False)
    confidence_score = Column(Numeric(5, 4), nullable=False)
    status = Column(String(24), nullable=False, server_default="CANDIDATE", index=True)
    extractor_version = Column(String(40), nullable=False)
    provenance_json = Column(JSON_TYPE, nullable=False)
    review_status = Column(String(24), nullable=False, server_default="PENDING", index=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    interpretation = relationship("HistoricalContentInterpretation")
