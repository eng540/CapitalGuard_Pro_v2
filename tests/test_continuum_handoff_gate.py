from decimal import Decimal

import pytest

from capitalguard.application.services.continuum_handoff_gate import (
    ContinuumHandoffFacts,
    ContinuumHandoffGate,
    HandoffStatus,
)
from capitalguard.domain.protection_policy import ProtectionPolicy


@pytest.fixture
def valid_facts():
    return ContinuumHandoffFacts(
        parse_complete=True,
        source_trusted=True,
        replay_evidence_verified=True,
        lifecycle_status="ACTIVE",
        duplicate_exists=False,
        protection_policy=ProtectionPolicy(
            mode="TRAILING",
            active=True,
            side="LONG",
            entry=Decimal("100"),
            stop_loss=Decimal("95"),
            trailing_value=Decimal("5"),
        ),
        consent_given=True,
        idempotency_key="forward-handoff-001",
        audit_ready=True,
    )


def test_valid_facts_approve_decision_without_side_effects(valid_facts):
    decision = ContinuumHandoffGate().evaluate(valid_facts)

    assert decision.status is HandoffStatus.APPROVED
    assert decision.approved is True
    assert decision.blocked is False
    assert decision.reason_codes == ()


def test_missing_consent_blocks_handoff(valid_facts):
    facts = ContinuumHandoffFacts(**{**valid_facts.__dict__, "consent_given": False})

    decision = ContinuumHandoffGate().evaluate(facts)

    assert decision.status is HandoffStatus.BLOCKED
    assert "EXPLICIT_CONSENT_REQUIRED" in decision.reason_codes


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("parse_complete", False, "PARSE_INCOMPLETE"),
        ("source_trusted", False, "SOURCE_NOT_TRUSTED"),
        ("replay_evidence_verified", False, "REPLAY_EVIDENCE_UNVERIFIED"),
        ("lifecycle_status", "CLOSED", "LIFECYCLE_NOT_ACTIVE"),
        ("duplicate_exists", True, "LIVE_ENTITY_ALREADY_EXISTS"),
        ("protection_policy", None, "PROTECTION_POLICY_INVALID_OR_MISSING"),
        ("idempotency_key", "", "IDEMPOTENCY_KEY_REQUIRED"),
        ("audit_ready", False, "AUDIT_CONTEXT_NOT_READY"),
    ],
)
def test_each_safety_prerequisite_is_enforced(valid_facts, field, value, reason):
    facts = ContinuumHandoffFacts(**{**valid_facts.__dict__, field: value})

    decision = ContinuumHandoffGate().evaluate(facts)

    assert decision.approved is False
    assert reason in decision.reason_codes


def test_invalid_protection_policy_mapping_blocks_handoff(valid_facts):
    facts = ContinuumHandoffFacts(
        **{
            **valid_facts.__dict__,
            "protection_policy": {
                "profit_stop_mode": "TRAILING",
                "profit_stop_active": True,
                "side": "LONG",
                "entry": "100",
                "stop_loss": "101",
                "profit_stop_trailing_value": "5",
            },
        }
    )

    decision = ContinuumHandoffGate().evaluate(facts)

    assert decision.blocked is True
    assert "PROTECTION_POLICY_INVALID_OR_MISSING" in decision.reason_codes


def test_block_reasons_are_complete_and_actionable(valid_facts):
    facts = ContinuumHandoffFacts(
        **{
            **valid_facts.__dict__,
            "parse_complete": False,
            "source_trusted": False,
            "replay_evidence_verified": False,
            "lifecycle_status": "PENDING_REVIEW",
            "duplicate_exists": True,
            "consent_given": False,
            "idempotency_key": None,
            "audit_ready": False,
        }
    )

    decision = ContinuumHandoffGate().evaluate(facts)

    assert decision.approved is False
    assert set(decision.reason_codes) >= {
        "PARSE_INCOMPLETE",
        "SOURCE_NOT_TRUSTED",
        "REPLAY_EVIDENCE_UNVERIFIED",
        "LIFECYCLE_NOT_ACTIVE",
        "LIVE_ENTITY_ALREADY_EXISTS",
        "EXPLICIT_CONSENT_REQUIRED",
        "IDEMPOTENCY_KEY_REQUIRED",
        "AUDIT_CONTEXT_NOT_READY",
    }
