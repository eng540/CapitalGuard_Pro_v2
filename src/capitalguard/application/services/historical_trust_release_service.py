from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from capitalguard.config import settings

from .historical_reputation_service import HistoricalReputationService


@dataclass(frozen=True)
class HistoricalTrustReleaseReadiness:
    status: str
    reasons: list[str]
    public_ranking_enabled: bool
    commercial_enabled: bool
    sample_size: int
    replay_coverage_percent: Decimal
    reviewed_attributions: int
    pending_attributions: int


class HistoricalTrustReleaseService:
    """Fail-closed operational gate; it cannot enable commerce or mutate live records."""

    @staticmethod
    def evaluate(session: Session, *, analyst_id: int | None = None, channel_id: int | None = None) -> HistoricalTrustReleaseReadiness:
        quality = HistoricalReputationService.quality_report(session, analyst_id=analyst_id, channel_id=channel_id)
        reasons: list[str] = []
        if not settings.HISTORICAL_TRUST_PUBLIC_RANKING_ENABLED:
            reasons.append("PUBLIC_RANKING_DISABLED")
        if quality.total_signals < settings.HISTORICAL_TRUST_MIN_SAMPLE_SIZE:
            reasons.append("MINIMUM_SAMPLE_NOT_MET")
        if quality.replay_coverage_percent < Decimal(str(settings.HISTORICAL_TRUST_MIN_REPLAY_COVERAGE_PERCENT)):
            reasons.append("REPLAY_COVERAGE_INSUFFICIENT")
        if quality.reviewed_attributions < quality.total_signals:
            reasons.append("ATTRIBUTION_REVIEW_INCOMPLETE")
        if quality.pending_attributions:
            reasons.append("ATTRIBUTION_REVIEW_PENDING")
        if quality.rank_eligible_signals == 0:
            reasons.append("NO_RANK_ELIGIBLE_SIGNALS")
        return HistoricalTrustReleaseReadiness(
            status="HOLD" if reasons else "READY_FOR_OWNER_RELEASE",
            reasons=reasons,
            public_ranking_enabled=False,
            commercial_enabled=False,
            sample_size=quality.total_signals,
            replay_coverage_percent=quality.replay_coverage_percent,
            reviewed_attributions=quality.reviewed_attributions,
            pending_attributions=quality.pending_attributions,
        )
