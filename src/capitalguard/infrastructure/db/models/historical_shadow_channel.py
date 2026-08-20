from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from .base import Base, JSON_TYPE


class HistoricalShadowChannel(Base):
    """Discovered Telegram source kept separate from claimed canonical channels."""

    __tablename__ = "historical_shadow_channels"

    id = Column(Integer, primary_key=True)
    telegram_channel_id = Column(BigInteger, nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=True)
    username = Column(String(255), nullable=True)
    claim_status = Column(String(24), nullable=False, server_default="UNCLAIMED", index=True)
    discovered_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    canonical_channel_catalog_id = Column(
        Integer,
        ForeignKey("channel_catalog.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sample_count = Column(Integer, nullable=False, server_default="0")
    metadata_json = Column(JSON_TYPE, nullable=True)

    discovered_by = relationship("User")
    canonical_channel_catalog = relationship("ChannelCatalog")
