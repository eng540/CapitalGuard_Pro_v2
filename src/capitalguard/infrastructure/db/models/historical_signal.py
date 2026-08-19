"""Historical signal evidence and reconstruction models.

These records are read-only historical evidence and must not enter live
recommendation lifecycle or publication outbox flows.
"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import relationship

from .base import Base, JSON_TYPE


class HistoricalImportBatch(Base):
    __tablename__ = "historical_import_batches"

    id = Column(Integer, primary_key=True)
    batch_ref = Column(String(48), nullable=False, unique=True, index=True)
    channel_catalog_id = Column(Integer, ForeignKey("channel_catalog.id", ondelete="SET NULL"), nullable=True, index=True)
    source_kind = Column(String(32), nullable=False, index=True)
    requested_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(24), nullable=False, server_default="DRY_RUN", index=True)
    manifest_hash = Column(String(64), nullable=False)
    total_records = Column(Integer, nullable=False, server_default="0")
    accepted_records = Column(Integer, nullable=False, server_default="0")
    rejected_records = Column(Integer, nullable=False, server_default="0")
    metadata_json = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    channel_catalog = relationship("ChannelCatalog")
    requested_by = relationship("User")
    evidence = relationship("HistoricalSignalEvidence", back_populates="batch")


class HistoricalSignalEvidence(Base):
    __tablename__ = "historical_signal_evidence"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("historical_import_batches.id", ondelete="SET NULL"), nullable=True, index=True)
    channel_catalog_id = Column(Integer, ForeignKey("channel_catalog.id", ondelete="SET NULL"), nullable=True, index=True)
    telegram_channel_id = Column(BigInteger, nullable=True, index=True)
    telegram_message_id = Column(BigInteger, nullable=True, index=True)
    message_revision = Column(Integer, nullable=False, server_default="0")
    source_kind = Column(String(32), nullable=False, index=True)
    source_uri = Column(String(500), nullable=True)
    message_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    raw_text = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    dedup_key = Column(String(180), nullable=False, unique=True)
    ownership_proof_type = Column(String(40), nullable=True)
    ownership_proof_ref = Column(String(500), nullable=True)
    evidence_confidence = Column(Numeric(5, 4), nullable=False, server_default="0")
    metadata_json = Column(JSON_TYPE, nullable=True)

    batch = relationship("HistoricalImportBatch", back_populates="evidence")
    channel_catalog = relationship("ChannelCatalog")
    signals = relationship("HistoricalSignal", back_populates="evidence", cascade="all, delete-orphan")


class HistoricalSignal(Base):
    __tablename__ = "historical_signals"

    id = Column(Integer, primary_key=True)
    public_ref = Column(String(48), unique=True, nullable=False, index=True)
    evidence_id = Column(Integer, ForeignKey("historical_signal_evidence.id", ondelete="RESTRICT"), nullable=False, index=True)
    channel_catalog_id = Column(Integer, ForeignKey("channel_catalog.id", ondelete="SET NULL"), nullable=True, index=True)
    channel_id = Column(Integer, ForeignKey("channels.id", ondelete="SET NULL"), nullable=True, index=True)
    analyst_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    asset = Column(String(80), nullable=True, index=True)
    side = Column(String(16), nullable=True)
    entry = Column(Numeric(20, 8), nullable=True)
    stop_loss = Column(Numeric(20, 8), nullable=True)
    targets = Column(JSON_TYPE, nullable=True)
    market = Column(String(80), nullable=True, index=True)
    decision_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(24), nullable=False, server_default="IMPORTED", index=True)
    trust_tier = Column(String(32), nullable=False, server_default="UNVERIFIED", index=True)
    confidence_score = Column(Numeric(5, 4), nullable=False, server_default="0")
    eligible_for_ranking = Column(Boolean, nullable=False, server_default="false", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    evidence = relationship("HistoricalSignalEvidence", back_populates="signals")
    channel_catalog = relationship("ChannelCatalog")
    channel = relationship("Channel")
    analyst = relationship("User")
    events = relationship("HistoricalSignalEvent", back_populates="signal", cascade="all, delete-orphan")
    attributions = relationship("HistoricalSignalAttribution", back_populates="signal", cascade="all, delete-orphan")


class HistoricalSignalEvent(Base):
    __tablename__ = "historical_signal_events"

    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer, ForeignKey("historical_signals.id", ondelete="CASCADE"), nullable=False, index=True)
    source_evidence_id = Column(Integer, ForeignKey("historical_signal_evidence.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(40), nullable=False, index=True)
    event_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    market_as_of = Column(DateTime(timezone=True), nullable=True, index=True)
    data_source = Column(String(80), nullable=True)
    price = Column(Numeric(20, 8), nullable=True)
    replay_status = Column(String(24), nullable=False, server_default="UNVERIFIED", index=True)
    event_confidence = Column(Numeric(5, 4), nullable=False, server_default="0")
    event_data = Column(JSON_TYPE, nullable=True)
    dedup_key = Column(String(180), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    signal = relationship("HistoricalSignal", back_populates="events")
    source_evidence = relationship("HistoricalSignalEvidence")


class HistoricalSignalAttribution(Base):
    __tablename__ = "historical_signal_attributions"

    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer, ForeignKey("historical_signals.id", ondelete="CASCADE"), nullable=False, index=True)
    attribution_kind = Column(String(24), nullable=False, index=True)
    analyst_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    channel_id = Column(Integer, ForeignKey("channels.id", ondelete="SET NULL"), nullable=True, index=True)
    trader_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    proof_type = Column(String(40), nullable=True)
    proof_ref = Column(String(500), nullable=True)
    confidence_score = Column(Numeric(5, 4), nullable=False, server_default="0")
    status = Column(String(24), nullable=False, server_default="PROPOSED", index=True)
    dedup_key = Column(String(180), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    signal = relationship("HistoricalSignal", back_populates="attributions")
    analyst = relationship("User", foreign_keys=[analyst_id])
    channel = relationship("Channel")
    trader_user = relationship("User", foreign_keys=[trader_user_id])
