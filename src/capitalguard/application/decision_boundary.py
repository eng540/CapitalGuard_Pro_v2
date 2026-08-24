"""G7 decision-to-operational-state boundary contract.

The contract is declarative and infrastructure-free. It does not create
recommendations, execute trades, or mutate lifecycle state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


DECISION_BOUNDARY_CONTRACT_VERSION = "g7.own.04"


class DecisionInputState(StrEnum):
    CANONICAL_ACCEPTED = "CANONICAL_ACCEPTED"
    CANONICAL_INCOMPLETE = "CANONICAL_INCOMPLETE"
    CANONICAL_AMBIGUOUS = "CANONICAL_AMBIGUOUS"
    AI_CANDIDATE = "AI_CANDIDATE"


class OperationalTarget(StrEnum):
    ANALYTICAL_RESULT = "ANALYTICAL_RESULT"
    RECOMMENDATION = "RECOMMENDATION"
    USER_TRADE = "USER_TRADE"
    EXECUTION_STATE = "EXECUTION_STATE"


@dataclass(frozen=True)
class DecisionBoundary:
    """Rules for moving a semantic input toward an operational target."""

    transition: str
    accepted_input: DecisionInputState
    target: OperationalTarget
    owner: str
    required_gate: str
    command_required: bool
    direct_ai_input_allowed: bool
    direct_web_input_allowed: bool
    execution_allowed: bool
    audit_required: bool
    idempotency_required: bool
    failure_policy: str


DECISION_BOUNDARIES: tuple[DecisionBoundary, ...] = (
    DecisionBoundary(
        transition="CANONICAL_TO_ANALYSIS",
        accepted_input=DecisionInputState.CANONICAL_ACCEPTED,
        target=OperationalTarget.ANALYTICAL_RESULT,
        owner="Application decision use case",
        required_gate="canonical validation and provenance complete",
        command_required=False,
        direct_ai_input_allowed=False,
        direct_web_input_allowed=False,
        execution_allowed=False,
        audit_required=True,
        idempotency_required=True,
        failure_policy="reject or quarantine incomplete/ambiguous input",
    ),
    DecisionBoundary(
        transition="ANALYSIS_TO_RECOMMENDATION",
        accepted_input=DecisionInputState.CANONICAL_ACCEPTED,
        target=OperationalTarget.RECOMMENDATION,
        owner="CreationService through an application use case",
        required_gate="domain validation and recommendation policy",
        command_required=True,
        direct_ai_input_allowed=False,
        direct_web_input_allowed=False,
        execution_allowed=False,
        audit_required=True,
        idempotency_required=True,
        failure_policy="no recommendation on failed validation; preserve source and decision evidence",
    ),
    DecisionBoundary(
        transition="RECOMMENDATION_TO_USER_TRADE",
        accepted_input=DecisionInputState.CANONICAL_ACCEPTED,
        target=OperationalTarget.USER_TRADE,
        owner="LifecycleService through a typed command/use case",
        required_gate="authenticated owner/trader command and lifecycle state transition",
        command_required=True,
        direct_ai_input_allowed=False,
        direct_web_input_allowed=False,
        execution_allowed=False,
        audit_required=True,
        idempotency_required=True,
        failure_policy="reject invalid or repeated state transition without mutating source truth",
    ),
    DecisionBoundary(
        transition="USER_TRADE_TO_EXECUTION",
        accepted_input=DecisionInputState.CANONICAL_ACCEPTED,
        target=OperationalTarget.EXECUTION_STATE,
        owner="AutoTradeService through an explicit execution command",
        required_gate="risk decision, credentials, balance, AUTO_TRADE_ENABLED, and TRADE_LIVE_ENABLED",
        command_required=True,
        direct_ai_input_allowed=False,
        direct_web_input_allowed=False,
        execution_allowed=True,
        audit_required=True,
        idempotency_required=True,
        failure_policy="durable ORDER_PLACED or ORDER_FAILED; no silent success",
    ),
)


def decision_boundary_for(transition: str) -> DecisionBoundary:
    """Return the declared boundary for a transition."""

    normalized = transition.strip().upper()
    for boundary in DECISION_BOUNDARIES:
        if boundary.transition == normalized:
            return boundary
    raise KeyError(transition)


def validate_decision_boundaries() -> None:
    """Validate the safety invariants of the decision boundary contract."""

    names = [boundary.transition for boundary in DECISION_BOUNDARIES]
    if len(names) != len(set(names)):
        raise ValueError("Every decision transition must have one boundary")
    if any(boundary.direct_ai_input_allowed for boundary in DECISION_BOUNDARIES):
        raise ValueError("AI output cannot be a direct operational input")
    if any(boundary.direct_web_input_allowed for boundary in DECISION_BOUNDARIES):
        raise ValueError("Web input cannot bypass canonical/domain validation")
    if any(boundary.execution_allowed and not boundary.command_required for boundary in DECISION_BOUNDARIES):
        raise ValueError("Execution requires an explicit command")
    if any(boundary.execution_allowed and not boundary.idempotency_required for boundary in DECISION_BOUNDARIES):
        raise ValueError("Execution requires idempotency")
