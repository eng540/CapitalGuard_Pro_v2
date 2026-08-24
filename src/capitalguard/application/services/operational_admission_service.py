"""G7 admission policy for moving a prepared decision to an explicit command.

The policy is deliberately side-effect free. It does not create a
Recommendation, mutate lifecycle state, or invoke execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from capitalguard.application.decision_boundary import (
    DecisionInputState,
    OperationalTarget,
)
from capitalguard.application.services.operational_decision_service import (
    OperationalDecision,
    OperationalDecisionError,
)


class AdmissionStatus(StrEnum):
    READY_FOR_EXPLICIT_COMMAND = "READY_FOR_EXPLICIT_COMMAND"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


@dataclass(frozen=True)
class RecommendationAdmission:
    status: AdmissionStatus
    command_type: str
    decision_fingerprint: str
    trace_id: str
    actor_ref: str
    command_id: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "command_type": self.command_type,
            "decision_fingerprint": self.decision_fingerprint,
            "trace_id": self.trace_id,
            "actor_ref": self.actor_ref,
            "command_id": self.command_id,
            "payload": self.payload,
        }


class OperationalAdmissionService:
    """Admit a decision to an explicit command without applying the command."""

    def admit_recommendation(
        self,
        decision: OperationalDecision,
        *,
        actor_ref: str,
        command_id: str,
        command_type: str = "CREATE_RECOMMENDATION",
        review_required: bool = False,
    ) -> RecommendationAdmission:
        if decision.input_state != DecisionInputState.CANONICAL_ACCEPTED:
            raise OperationalDecisionError("only canonical accepted input can be admitted")
        if decision.target not in {OperationalTarget.ANALYTICAL_RESULT, OperationalTarget.RECOMMENDATION}:
            raise OperationalDecisionError("decision target is not eligible for recommendation admission")
        if not actor_ref.strip() or not command_id.strip():
            raise OperationalDecisionError("actor_ref and command_id are required")
        if not command_type.strip():
            raise OperationalDecisionError("command_type is required")
        return RecommendationAdmission(
            status=(AdmissionStatus.REQUIRES_REVIEW if review_required else AdmissionStatus.READY_FOR_EXPLICIT_COMMAND),
            command_type=command_type.strip().upper(),
            decision_fingerprint=decision.decision_fingerprint,
            trace_id=decision.trace.trace_id,
            actor_ref=actor_ref.strip(),
            command_id=command_id.strip(),
            payload={
                "canonical": dict(decision.canonical),
                "evidence": dict(decision.evidence),
                "execution_allowed": False,
            },
        )

    @staticmethod
    def validate_admission_payload(payload: Mapping[str, Any]) -> None:
        """Reject payloads that attempt to turn an admission into execution."""

        if bool(payload.get("execution_allowed")):
            raise OperationalDecisionError("recommendation admission cannot authorize execution")
