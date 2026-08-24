import pytest

from capitalguard.application.services.operational_decision_service import (
    OperationalDecisionError,
    OperationalDecisionService,
)


VALID_CANONICAL = {
    "asset": "btcusdt",
    "direction": "long",
    "entry": "77000",
    "stop_loss": "76000",
    "targets": ["78000"],
    "market": "futures",
}


def test_canonical_input_is_normalized_for_analysis_only():
    decision = OperationalDecisionService().prepare(
        VALID_CANONICAL,
        evidence={"source": "historical-revision-1", "modality": "TEXT"},
    )
    assert decision.status == "READY_FOR_ANALYSIS"
    assert decision.canonical == {
        "asset": "BTCUSDT",
        "direction": "LONG",
        "entry": "77000",
        "stop_loss": "76000",
        "targets": ["78000"],
        "market": "FUTURES",
    }
    assert len(decision.decision_fingerprint) == 64
    assert decision.execution_allowed is False
    assert decision.trace.source_ref == "historical-revision-1"
    assert decision.trace.correlation_id
    assert decision.trace.input_hash
    assert decision.trace.contract_version == "g7.con.02"


def test_equivalent_canonical_input_has_stable_fingerprint():
    service = OperationalDecisionService()
    first = service.prepare(VALID_CANONICAL, evidence={"source": "same"})
    second = service.prepare({**VALID_CANONICAL, "asset": "BTCUSDT"}, evidence={"source": "same"})
    assert first.decision_fingerprint == second.decision_fingerprint


def test_incomplete_or_invalid_canonical_input_is_rejected():
    with pytest.raises(OperationalDecisionError):
        OperationalDecisionService().prepare({"asset": "BTCUSDT"})
    with pytest.raises(OperationalDecisionError):
        OperationalDecisionService().prepare({**VALID_CANONICAL, "entry": "0"})
    with pytest.raises(OperationalDecisionError):
        OperationalDecisionService().prepare({**VALID_CANONICAL, "direction": "UNKNOWN"})


def test_decision_trace_preserves_correlation_and_causation():
    decision = OperationalDecisionService().prepare(
        VALID_CANONICAL,
        evidence={
            "source_ref": "revision:42",
            "correlation_id": "batch:7",
            "causation_id": "telegram:99",
        },
    )
    assert decision.trace.source_ref == "revision:42"
    assert decision.trace.correlation_id == "batch:7"
    assert decision.trace.causation_id == "telegram:99"
    assert decision.as_dict()["trace"]["trace_id"] == decision.trace.trace_id


def test_missing_source_ref_is_rejected():
    with pytest.raises(OperationalDecisionError, match="source_ref"):
        OperationalDecisionService().prepare(VALID_CANONICAL, evidence={"modality": "TEXT"})


def test_execution_cannot_bypass_explicit_command_boundary():
    from capitalguard.application.decision_boundary import OperationalTarget

    with pytest.raises(OperationalDecisionError):
        OperationalDecisionService().prepare(
            VALID_CANONICAL,
            target=OperationalTarget.EXECUTION_STATE,
        )
