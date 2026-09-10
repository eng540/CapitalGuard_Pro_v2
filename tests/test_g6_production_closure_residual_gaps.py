from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from capitalguard.application.services.historical_replay_decision_service import (
    HistoricalReplayDecisionAuthority,
    ReplayAction,
    ReplayDecisionReason,
)


CURRENT = "G6-R2"
POLICY = "G6-OHLCV-MARKET-GRID-3"


def replay(**overrides):
    values = {
        "id": 251,
        "signal_id": 11,
        "materialization_id": 21,
        "status": "COMPLETED",
        "replay_version": CURRENT,
        "policy_version": POLICY,
        "coverage_status": "FULL",
        "coverage_ratio": 1.0,
        "request_fingerprint": "fp-old",
        "result_json": {"lifecycle_status": "PENDING_ORDER"},
        "provider_metadata": {"requested_start": "2025-01-01T00:00:00+00:00", "requested_end": "2025-01-02T00:00:00+00:00"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_duplicate_reuse_has_explicit_audit_record():
    previous = replay()
    decision = HistoricalReplayDecisionAuthority(replay_version=CURRENT, policy_version=POLICY).decide(previous)
    assert decision.action is ReplayAction.REUSE
    assert decision.reason is ReplayDecisionReason.CURRENT_REPLAY_VALID
    audit = decision.audit_record(batch_id=2510, receipt_id=2511, signal_id=11, materialization_id=21)
    assert audit == {
        "batch": 2510,
        "receipt": 2511,
        "signal": 11,
        "materialization": 21,
        "decision": "REUSE",
        "reason": "CURRENT_REPLAY_VALID",
        "previous_run": 251,
        "engine_prev": CURRENT,
        "engine_current": CURRENT,
        "policy_prev": POLICY,
        "policy_current": POLICY,
        "coverage": "FULL_COVERAGE_NO_ACTIVATION_RISK",
        "coverage_valid": True,
        "new_run": None,
        "lineage_parent": None,
        "fingerprint": None,
    }


def test_duplicate_reprocess_has_explicit_audit_record():
    previous = replay(status="REPLAY_PARTIAL", coverage_status="PARTIAL_WINDOW")
    decision = HistoricalReplayDecisionAuthority(replay_version=CURRENT, policy_version=POLICY).decide(previous)
    assert decision.action is ReplayAction.REPROCESS
    assert decision.reason is ReplayDecisionReason.REPLAY_PARTIAL
    audit = decision.audit_record(
        batch_id=2380,
        receipt_id=2381,
        signal_id=11,
        materialization_id=21,
        new_run_id=252,
        lineage_parent=251,
        new_fingerprint="fp-new",
    )
    assert audit["decision"] == "REPROCESS"
    assert audit["reason"] == "REPLAY_PARTIAL"
    assert audit["previous_run"] == 251
    assert audit["new_run"] == 252
    assert audit["lineage_parent"] == 251
    assert audit["fingerprint"] == "fp-new"


def test_stale_completed_reprocess_is_not_reuse():
    previous = replay(replay_version="G6-R1", policy_version="G6-OHLCV-MARKET-GRID-2")
    decision = HistoricalReplayDecisionAuthority(replay_version=CURRENT, policy_version=POLICY).decide(previous)
    assert decision.action is ReplayAction.REPROCESS
    assert decision.reason is ReplayDecisionReason.STALE_ENGINE_VERSION


def test_current_completed_invalid_coverage_reprocesses():
    previous = replay(coverage_status="PARTIAL_WINDOW")
    decision = HistoricalReplayDecisionAuthority(replay_version=CURRENT, policy_version=POLICY).decide(previous)
    assert decision.action is ReplayAction.REPROCESS
    assert decision.reason is ReplayDecisionReason.INVALID_COVERAGE
    assert decision.coverage.reason == "COVERAGE_PARTIAL_WINDOW"


def test_coverage_gap_that_can_hide_activation_blocks_reuse():
    previous = replay(result_json={"lifecycle_status": "PENDING_ORDER", "activation_risk": True})
    decision = HistoricalReplayDecisionAuthority(replay_version=CURRENT, policy_version=POLICY).decide(previous)
    assert decision.action is ReplayAction.REPROCESS
    assert decision.reason is ReplayDecisionReason.INVALID_COVERAGE
    assert decision.coverage.reason == "GAP_CAN_HIDE_ACTIVATION"


def test_recent_replay_window_is_clamped_by_planner_not_future_extended():
    from capitalguard.application.services.adaptive_historical_replay import AdaptiveHistoricalReplayPlanner

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    source = now - timedelta(hours=1)
    window = AdaptiveHistoricalReplayPlanner.minute_window_for_day(day=source, signal_source_time=source, now=now)
    assert window is not None
    assert window.end <= now
