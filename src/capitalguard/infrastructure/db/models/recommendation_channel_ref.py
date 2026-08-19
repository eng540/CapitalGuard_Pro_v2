"""Per-channel recommendation identity for multi-channel publication."""
from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


class RecommendationChannelRef(Base):
    __tablename__ = "recommendation_channel_refs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recommendation_id = Column(Integer, ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_catalog_id = Column(Integer, ForeignKey("channel_catalog.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_sequence = Column(Integer, nullable=False)

    recommendation = relationship("Recommendation", back_populates="channel_refs")
    channel_catalog = relationship("ChannelCatalog", back_populates="recommendation_refs")

    __table_args__ = (
        UniqueConstraint("recommendation_id", "channel_catalog_id", name="uq_recommendation_channel_ref"),
        UniqueConstraint("channel_catalog_id", "channel_sequence", name="uq_channel_recommendation_sequence"),
    )
