from types import SimpleNamespace

import pytest

from capitalguard.application.services.historical_replay_decision_service import (
    CURRENT_REPLAY_POLICY_VERSION,
    CURRENT_REPLAY_VERSION,
    HistoricalReplayDecisionAuthority,
    ReplayAction,
    ReplayDecisionReason,
)


AUTHORITY = HistoricalReplayDecisionAuthority()


def run(*, status, version=CURRENT_REPLAY_VERSION, policy=CURRENT_REPLAY_POLICY_VERSION, coverage="FULL", activation_risk=False, run_id=1, fingerprint="fp-1", signal_id=10, materialization_id=20, parent=None):
    return SimpleNamespace(
        id=run_id,
        status=status,
        replay_version=version,
        policy_version=policy,
        coverage_status=coverage,
        coverage_ratio=1.0 if coverage == "FULL" else 0.5,
        request_fingerprint=fingerprint,
        signal_id=signal_id,
        materialization_id=materialization_id,
        reprocess_of_run_id=parent,
        reprocess_of=parent,
        result_json={"activation_risk": activation_risk},
        provider_metadata={"gaps": []},
    )


def test_replay_decision_matrix():
    cases = [
        (None, ReplayAction.REPROCESS, ReplayDecisionReason.NO_PREVIOUS_REPLAY),
        (run(status="FAILED"), ReplayAction.REPROCESS, ReplayDecisionReason.FAILED),
        (run(status="REPLAY_PARTIAL"), ReplayAction.REPROCESS, ReplayDecisionReason.REPLAY_PARTIAL),
        (run(status="STILL_ACTIVE", version="G6-R1", policy="G6-OHLCV-MARKET-GRID-2"), ReplayAction.REPROCESS, ReplayDecisionReason.LEGACY_STILL_ACTIVE),
        (run(status="COMPLETED", version="G6-R1", policy="G6-OHLCV-MARKET-GRID-2"), ReplayAction.REPROCESS, ReplayDecisionReason.STALE_ENGINE_VERSION),
        (run(status="COMPLETED_UNVERIFIABLE", version="G6-R1", policy="G6-OHLCV-MARKET-GRID-2"), ReplayAction.REPROCESS, ReplayDecisionReason.STALE_ENGINE_VERSION),
        (run(status="COMPLETED"), ReplayAction.REUSE, ReplayDecisionReason.CURRENT_REPLAY_VALID),
        (run(status="COMPLETED_UNVERIFIABLE"), ReplayAction.REUSE, ReplayDecisionReason.CURRENT_REPLAY_VALID),
        (run(status="COMPLETED", coverage="PARTIAL_WINDOW"), ReplayAction.REPROCESS, ReplayDecisionReason.INVALID_COVERAGE),
    ]
    for previous, expected_action, expected_reason in cases:
        decision = AUTHORITY.decide(previous)
        assert decision.action is expected_action
        assert decision.reason is expected_reason


def test_reprocess_still_active_legacy_result():
    decision = AUTHORITY.decide(run(status="STILL_ACTIVE", version="G6-R1", policy="G6-OHLCV-MARKET-GRID-2"))
    assert decision.is_reprocess
    assert decision.reason is ReplayDecisionReason.LEGACY_STILL_ACTIVE


def test_current_completed_reuses_without_g6():
    decision = AUTHORITY.decide(run(status="COMPLETED"))
    assert decision.action is ReplayAction.REUSE


def test_current_completed_invalid_coverage_reprocesses():
    previous = run(status="COMPLETED", coverage="FULL", activation_risk=True)
    decision = AUTHORITY.decide(previous)
    assert decision.is_reprocess
    assert decision.reason is ReplayDecisionReason.INVALID_COVERAGE
    assert decision.coverage.reason == "GAP_CAN_HIDE_ACTIVATION"


def test_reprocess_preserves_lineage_and_identity():
    old = run(status="REPLAY_PARTIAL", run_id=1, fingerprint="fp-old")
    new = run(status="COMPLETED", run_id=2, fingerprint="fp-new", parent=1)
    new.reprocess_of = old
    AUTHORITY.assert_valid_lineage(previous_run=old, new_run=new)


def test_reprocess_requires_new_fingerprint_and_parent():
    old = run(status="FAILED", run_id=1, fingerprint="fp-old")
    same_fp = run(status="COMPLETED", run_id=2, fingerprint="fp-old", parent=1)
    same_fp.reprocess_of = old
    with pytest.raises(ValueError, match="fingerprint"):
        AUTHORITY.assert_valid_lineage(previous_run=old, new_run=same_fp)


def test_lineage_cycle_is_rejected():
    run1 = run(status="FAILED", run_id=1, fingerprint="fp-1")
    run2 = run(status="FAILED", run_id=2, fingerprint="fp-2", parent=1)
    run3 = run(status="FAILED", run_id=3, fingerprint="fp-3", parent=2)
    run2.reprocess_of = run3
    with pytest.raises(ValueError, match="cycle"):
        AUTHORITY.assert_valid_lineage(previous_run=run2, new_run=run3)


def test_98k_pending_order_requires_evidence_not_activated_flag():
    previous = run(status="COMPLETED", coverage="FULL", activation_risk=False)
    previous.result_json = {
        "lifecycle_status": "PENDING_ORDER",
        "activated": False,
        "activation_risk": False,
        "entry_ambiguity": False,
    }
    decision = AUTHORITY.decide(previous)
    assert decision.action is ReplayAction.REUSE
    assert decision.coverage.valid


def test_gap_capable_of_hiding_activation_blocks_pending_order_reuse():
    previous = run(status="COMPLETED", coverage="FULL", activation_risk=True)
    previous.result_json = {
        "lifecycle_status": "PENDING_ORDER",
        "activated": False,
        "activation_risk": True,
    }
    decision = AUTHORITY.decide(previous)
    assert decision.is_reprocess
    assert decision.coverage.reason == "GAP_CAN_HIDE_ACTIVATION"
