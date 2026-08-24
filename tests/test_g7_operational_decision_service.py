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


def test_execution_cannot_bypass_explicit_command_boundary():
    from capitalguard.application.decision_boundary import OperationalTarget

    with pytest.raises(OperationalDecisionError):
        OperationalDecisionService().prepare(
            VALID_CANONICAL,
            target=OperationalTarget.EXECUTION_STATE,
        )
