"""Confidence-separated historical reputation summaries."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalSignal, HistoricalSignalEvent


@dataclass(frozen=True)
class HistoricalReputationSummary:
    analyst_id: int | None
    channel_id: int | None
    total_signals: int
    verified_signals: int
    rank_eligible_signals: int
    excluded_signals: int
    verified_replay_events: int
    confidence_weighted_sample: Decimal


class HistoricalReputationService:
    """Read-only summary; it never writes to live AnalystStats."""

    @staticmethod
    def summarize(
        session: Session,
        *,
        analyst_id: int | None = None,
        channel_id: int | None = None,
    ) -> HistoricalReputationSummary:
        statement = select(HistoricalSignal)
        if analyst_id is not None:
            statement = statement.where(HistoricalSignal.analyst_id == analyst_id)
        if channel_id is not None:
            statement = statement.where(HistoricalSignal.channel_id == channel_id)
        signals = list(session.execute(statement).scalars().all())
        verified_tiers = {"VERIFIED_LIVE", "VERIFIED_HISTORY", "RECONSTRUCTED"}
        verified = [signal for signal in signals if signal.trust_tier in verified_tiers]
        eligible = [
            signal for signal in signals
            if signal.eligible_for_ranking and signal.trust_tier in verified_tiers
        ]
        weighted = sum(
            (Decimal(str(signal.confidence_score or 0)) for signal in eligible),
            Decimal("0"),
        )
        verified_events = session.execute(
            select(HistoricalSignalEvent).where(
                HistoricalSignalEvent.signal_id.in_([signal.id for signal in signals]),
                HistoricalSignalEvent.replay_status == "VERIFIED",
            )
        ).scalars().all() if signals else []
        return HistoricalReputationSummary(
            analyst_id=analyst_id,
            channel_id=channel_id,
            total_signals=len(signals),
            verified_signals=len(verified),
            rank_eligible_signals=len(eligible),
            excluded_signals=len(signals) - len(eligible),
            verified_replay_events=len(verified_events),
            confidence_weighted_sample=weighted,
        )
