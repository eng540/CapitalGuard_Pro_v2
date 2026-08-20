from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StabilityGateInput:
    tests_passed: int
    tests_failed: int
    skipped_tests: int
    outbox_queue_size: float
    live_entity_leaks: int
    unreviewed_financial_conflicts: int
    replay_pending_records: int


@dataclass(frozen=True)
class StabilityGateReport:
    status: str
    reasons: tuple[str, ...]
    commercial_enabled: bool
    copy_trading_enabled: bool


class ReleaseStabilityGateService:
    """Conservative R4/R5 gate; billing and copy trading remain disabled by design."""

    def evaluate(self, snapshot: StabilityGateInput) -> StabilityGateReport:
        reasons: list[str] = []
        if snapshot.tests_failed > 0:
            reasons.append("TEST_FAILURES")
        if snapshot.tests_passed <= 0:
            reasons.append("NO_TEST_EVIDENCE")
        if snapshot.outbox_queue_size > 0:
            reasons.append("OUTBOX_NOT_DRAINED")
        if snapshot.live_entity_leaks > 0:
            reasons.append("LIVE_ENTITY_LEAK_DETECTED")
        if snapshot.unreviewed_financial_conflicts > 0:
            reasons.append("FINANCIAL_CONFLICT_REVIEW_BACKLOG")
        if snapshot.replay_pending_records > 0:
            reasons.append("REPLAY_PENDING_BACKLOG")
        status = "PASS" if not reasons else "HOLD"
        return StabilityGateReport(
            status=status,
            reasons=tuple(reasons),
            commercial_enabled=False,
            copy_trading_enabled=False,
        )
