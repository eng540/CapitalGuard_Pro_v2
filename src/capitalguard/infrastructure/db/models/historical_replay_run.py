from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from .base import Base, JSON_TYPE


class HistoricalReplayRun(Base):
    """Traceable G6 execution identity, distinct from a database transaction."""

    __tablename__ = "historical_replay_runs"

    id = Column(Integer, primary_key=True)
    run_ref = Column(String(48), nullable=False, unique=True, index=True)
    signal_id = Column(Integer, ForeignKey("historical_signals.id", ondelete="RESTRICT"), nullable=False, index=True)
    materialization_id = Column(Integer, ForeignKey("historical_signal_materializations.id", ondelete="RESTRICT"), nullable=False, index=True)
    request_fingerprint = Column(String(64), nullable=False, unique=True, index=True)
    replay_version = Column(String(32), nullable=False)
    policy_version = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False, server_default="CREATED", index=True)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    interval = Column(String(16), nullable=False, server_default="1m")
    limit_count = Column(Integer, nullable=False, server_default="1500")
    provider = Column(String(80), nullable=True, index=True)
    provider_endpoint = Column(String(255), nullable=True)
    data_source = Column(String(80), nullable=True)
    provider_metadata = Column(JSON_TYPE, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    data_as_of_status = Column(String(40), nullable=False, server_default="UNAVAILABLE")
    ambiguity_status = Column(String(24), nullable=False, server_default="NONE")
    quality_status = Column(String(24), nullable=False, server_default="UNASSESSED")
    result_json = Column(JSON_TYPE, nullable=True)
    failure_reason = Column(String(500), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    signal = relationship("HistoricalSignal")
    materialization = relationship("HistoricalSignalMaterialization")
    market_evidence = relationship("HistoricalMarketEvidence", back_populates="replay_run")
    events = relationship("HistoricalSignalEvent", back_populates="replay_run")
