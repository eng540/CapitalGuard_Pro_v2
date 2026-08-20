from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .historical_outcome_reconciliation_service import TimelineEventInput, HistoricalOutcomeReconciliationService


@dataclass(frozen=True)
class ReplayGateReport:
    status: str
    replay_allowed: bool
    reputation_eligible: bool
    reason_codes: tuple[str, ...]


class HistoricalReplayGateService:
    """Prevents unverified outcomes from silently entering replay-derived reputation."""

    def __init__(self, reconciliation_service: HistoricalOutcomeReconciliationService | None = None):
        self.reconciliation_service = reconciliation_service or HistoricalOutcomeReconciliationService()

    def assess(
        self,
        *,
        parse_status: str,
        financial_outcome: dict[str, Any] | None = None,
        timeline_events: Iterable[TimelineEventInput] = (),
        market_data_available: bool = False,
    ) -> ReplayGateReport:
        reasons: list[str] = []
        if parse_status != "PARSED":
            return ReplayGateReport(
                "BLOCKED_PARSE_INCOMPLETE",
                replay_allowed=False,
                reputation_eligible=False,
                reason_codes=("PARSER_NOT_COMPLETE",),
            )
        timeline = self.reconciliation_service.reconcile_timeline(timeline_events)
        if not timeline.is_consistent:
            reasons.extend(timeline.errors)
            return ReplayGateReport(
                "OWNER_REVIEW_REQUIRED",
                replay_allowed=False,
                reputation_eligible=False,
                reason_codes=tuple(reasons + ["TIMELINE_RECONCILIATION_FAILED"]),
            )
        outcome_status = (financial_outcome or {}).get("status")
        if outcome_status == "MISMATCH":
            reasons.extend((financial_outcome or {}).get("warnings") or [])
            reasons.append("SOURCE_RESULT_REQUIRES_REVIEW")
        if not market_data_available:
            reasons.append("HISTORICAL_OHLCV_MISSING")
            return ReplayGateReport(
                "OWNER_REVIEW_REQUIRED" if outcome_status == "MISMATCH" else "REPLAY_PENDING",
                replay_allowed=False,
                reputation_eligible=False,
                reason_codes=tuple(dict.fromkeys(reasons)),
            )
        if outcome_status == "MISMATCH":
            return ReplayGateReport(
                "OWNER_REVIEW_REQUIRED",
                replay_allowed=True,
                reputation_eligible=False,
                reason_codes=tuple(dict.fromkeys(reasons)),
            )
        return ReplayGateReport(
            "REPLAY_READY",
            replay_allowed=True,
            reputation_eligible=True,
            reason_codes=("PARSER_COMPLETE", "TIMELINE_CONSISTENT", "HISTORICAL_OHLCV_AVAILABLE"),
        )
