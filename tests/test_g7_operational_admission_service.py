import pytest

from capitalguard.application.services.operational_admission_service import (
    AdmissionStatus,
    OperationalAdmissionService,
)
from capitalguard.application.services.operational_decision_service import (
    OperationalDecisionError,
    OperationalDecisionService,
)


CANONICAL = {
    "asset": "BTCUSDT",
    "direction": "LONG",
    "entry": "77000",
    "stop_loss": "76000",
    "targets": ["78000"],
    "market": "FUTURES",
}


def _decision():
    return OperationalDecisionService().prepare(
        CANONICAL,
        evidence={"source_ref": "revision:1", "correlation_id": "batch:1"},
    )


def test_admission_requires_explicit_actor_and_command():
    admission = OperationalAdmissionService().admit_recommendation(
        _decision(), actor_ref="analyst:7", command_id="command:42"
    )
    assert admission.status == AdmissionStatus.READY_FOR_EXPLICIT_COMMAND
    assert admission.actor_ref == "analyst:7"
    assert admission.command_id == "command:42"
    assert admission.trace_id
    assert admission.decision_fingerprint
    assert admission.payload["execution_allowed"] is False


def test_admission_can_require_review_without_mutating_decision():
    admission = OperationalAdmissionService().admit_recommendation(
        _decision(), actor_ref="owner:1", command_id="command:43", review_required=True
    )
    assert admission.status == AdmissionStatus.REQUIRES_REVIEW
    assert admission.payload["canonical"]["asset"] == "BTCUSDT"


def test_admission_rejects_missing_identity_and_execution_payload():
    with pytest.raises(OperationalDecisionError):
        OperationalAdmissionService().admit_recommendation(_decision(), actor_ref="", command_id="command:1")
    with pytest.raises(OperationalDecisionError):
        OperationalAdmissionService().validate_admission_payload({"execution_allowed": True})
