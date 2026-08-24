import pytest

from capitalguard.application.decision_boundary import (
    DECISION_BOUNDARIES,
    DecisionInputState,
    OperationalTarget,
    decision_boundary_for,
    validate_decision_boundaries,
)


def test_decision_boundaries_are_unique_and_safe():
    validate_decision_boundaries()
    names = [boundary.transition for boundary in DECISION_BOUNDARIES]
    assert len(names) == len(set(names))
    assert all(boundary.audit_required for boundary in DECISION_BOUNDARIES)
    assert all(boundary.idempotency_required for boundary in DECISION_BOUNDARIES)
    assert all(not boundary.direct_ai_input_allowed for boundary in DECISION_BOUNDARIES)
    assert all(not boundary.direct_web_input_allowed for boundary in DECISION_BOUNDARIES)


def test_canonical_to_recommendation_is_creation_owned():
    boundary = decision_boundary_for("analysis_to_recommendation")
    assert boundary.accepted_input == DecisionInputState.CANONICAL_ACCEPTED
    assert boundary.target == OperationalTarget.RECOMMENDATION
    assert boundary.owner.startswith("CreationService")
    assert boundary.command_required is True
    assert boundary.execution_allowed is False


def test_execution_requires_explicit_gated_command():
    boundary = decision_boundary_for("user_trade_to_execution")
    assert boundary.target == OperationalTarget.EXECUTION_STATE
    assert boundary.command_required is True
    assert boundary.execution_allowed is True
    assert "TRADE_LIVE_ENABLED" in boundary.required_gate
    assert "risk decision" in boundary.required_gate


def test_incomplete_or_ambiguous_input_is_rejected_at_boundary():
    for state in (DecisionInputState.CANONICAL_INCOMPLETE, DecisionInputState.CANONICAL_AMBIGUOUS, DecisionInputState.AI_CANDIDATE):
        assert state not in {boundary.accepted_input for boundary in DECISION_BOUNDARIES}


def test_unknown_transition_is_rejected():
    with pytest.raises(KeyError):
        decision_boundary_for("unknown")
