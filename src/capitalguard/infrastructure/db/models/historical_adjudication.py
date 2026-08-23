from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from .base import Base, JSON_TYPE

class HistoricalRecommendationDraft(Base):
    __tablename__ = "historical_recommendation_drafts"
    __table_args__ = (UniqueConstraint("revision_id", "draft_kind", name="uq_hist_draft_revision_kind"),)
    id = Column(Integer, primary_key=True)
    revision_id = Column(Integer, ForeignKey("historical_message_revisions.id", ondelete="CASCADE"), nullable=False, index=True)
    related_draft_id = Column(Integer, ForeignKey("historical_recommendation_drafts.id", ondelete="SET NULL"), nullable=True, index=True)
    draft_kind = Column(String(40), nullable=False, index=True)
    confidence_score = Column(Numeric(5,4), nullable=False)
    status = Column(String(24), nullable=False, server_default="DRAFT", index=True)
    evidence_chain_json = Column(JSON_TYPE, nullable=False)
    adjudication_reason = Column(Text, nullable=True)
    reviewed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)
    override_json = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
