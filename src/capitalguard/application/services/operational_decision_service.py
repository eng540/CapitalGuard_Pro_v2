"""G7 canonical-to-decision handoff.

This use case is intentionally side-effect free. It validates an already
materialized canonical projection and prepares a traceable decision input for a
later, explicitly authorized Recommendation command.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from capitalguard.application.decision_boundary import (
    DecisionInputState,
    OperationalTarget,
    decision_boundary_for,
)
from capitalguard.application.decision_trace import DecisionTrace


class OperationalDecisionError(ValueError):
    """Raised when canonical input cannot cross the decision boundary."""


@dataclass(frozen=True)
class OperationalDecision:
    """Traceable handoff with no persistence or external side effect."""

    status: str
    target: OperationalTarget
    input_state: DecisionInputState
    decision_fingerprint: str
    canonical: dict[str, Any]
    evidence: dict[str, Any]
    trace: DecisionTrace
    execution_allowed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target.value,
            "input_state": self.input_state.value,
            "decision_fingerprint": self.decision_fingerprint,
            "canonical": self.canonical,
            "evidence": self.evidence,
            "trace": self.trace.as_dict(),
            "execution_allowed": self.execution_allowed,
        }


class OperationalDecisionService:
    """Prepare canonical input for an application decision use case."""

    REQUIRED_FIELDS = (
        "asset",
        "direction",
        "entry",
        "stop_loss",
        "targets",
        "market",
    )

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise OperationalDecisionError(
                f"{field} must be a finite positive number"
            ) from exc
        if not number.is_finite() or number <= 0:
            raise OperationalDecisionError(
                f"{field} must be a finite positive number"
            )
        return number

    @classmethod
    def _normalize_canonical(
        cls, canonical: Mapping[str, Any]
    ) -> dict[str, Any]:
        normalized = dict(canonical)
        missing = [
            field for field in cls.REQUIRED_FIELDS if not normalized.get(field)
        ]
        if missing:
            raise OperationalDecisionError(
                f"canonical input is incomplete: {', '.join(missing)}"
            )
        normalized["asset"] = str(normalized["asset"]).strip().upper()
        normalized["direction"] = str(normalized["direction"]).strip().upper()
        normalized["market"] = str(normalized["market"]).strip().upper()
        if normalized["direction"] not in {"LONG", "SHORT"}:
            raise OperationalDecisionError("direction must be LONG or SHORT")
        if normalized["market"] not in {"SPOT", "FUTURES"}:
            raise OperationalDecisionError("market must be SPOT or FUTURES")
        normalized["entry"] = str(cls._decimal(normalized["entry"], "entry"))
        normalized["stop_loss"] = str(
            cls._decimal(normalized["stop_loss"], "stop_loss")
        )
        targets = normalized["targets"]
        if not isinstance(targets, (list, tuple)) or not targets:
            raise OperationalDecisionError("targets must be a non-empty list")
        normalized_targets: list[str] = []
        for index, target in enumerate(targets, start=1):
            value = (
                target.get("value") if isinstance(target, Mapping) else target
            )
            normalized_targets.append(
                str(cls._decimal(value, f"target[{index}]"))
            )
        normalized["targets"] = normalized_targets
        return normalized

    @staticmethod
    def _fingerprint(
        canonical: Mapping[str, Any], evidence: Mapping[str, Any]
    ) -> str:
        encoded = json.dumps(
            {"canonical": canonical, "evidence": evidence},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _prepare_transition(
        self,
        canonical: Mapping[str, Any],
        *,
        evidence: Mapping[str, Any] | None,
        target: OperationalTarget,
        transition: str,
        actor_ref: str | None = None,
        command_id: str | None = None,
    ) -> OperationalDecision:
        boundary = decision_boundary_for(transition)
        if boundary.target != target:
            raise OperationalDecisionError("decision boundary target mismatch")
        normalized = self._normalize_canonical(canonical)
        evidence_payload = dict(evidence or {})
        source_ref = str(
            evidence_payload.get("source_ref")
            or evidence_payload.get("source")
            or ""
        ).strip()
        if not source_ref:
            raise OperationalDecisionError(
                "source_ref is required for operational decision traceability"
            )
        if target == OperationalTarget.RECOMMENDATION:
            if (
                not actor_ref
                or not actor_ref.strip()
                or not command_id
                or not command_id.strip()
            ):
                raise OperationalDecisionError(
                    "recommendation transition requires actor_ref and command_id"
                )
            evidence_payload.setdefault("actor_ref", actor_ref.strip())
            evidence_payload.setdefault("command_id", command_id.strip())
        fingerprint = self._fingerprint(normalized, evidence_payload)
        trace = DecisionTrace.build(
            source_ref=source_ref,
            input_payload={"canonical": normalized, "evidence": evidence_payload},
            correlation_id=evidence_payload.get("correlation_id") or command_id,
            causation_id=evidence_payload.get("causation_id") or actor_ref,
        )
        return OperationalDecision(
            status=(
                "READY_FOR_ANALYSIS"
                if target == OperationalTarget.ANALYTICAL_RESULT
                else "READY_FOR_RECOMMENDATION"
            ),
            target=target,
            input_state=DecisionInputState.CANONICAL_ACCEPTED,
            decision_fingerprint=fingerprint,
            canonical=normalized,
            evidence=evidence_payload,
            trace=trace,
            execution_allowed=False,
        )

    def prepare(
        self,
        canonical: Mapping[str, Any],
        *,
        evidence: Mapping[str, Any] | None = None,
        target: OperationalTarget = OperationalTarget.ANALYTICAL_RESULT,
    ) -> OperationalDecision:
        """Prepare analysis only; recommendation requires an explicit method."""

        if target != OperationalTarget.ANALYTICAL_RESULT:
            raise OperationalDecisionError("prepare() only supports ANALYTICAL_RESULT")
        return self._prepare_transition(
            canonical,
            evidence=evidence,
            target=OperationalTarget.ANALYTICAL_RESULT,
            transition="CANONICAL_TO_ANALYSIS",
        )

    def prepare_recommendation(
        self,
        canonical: Mapping[str, Any],
        *,
        actor_ref: str,
        command_id: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> OperationalDecision:
        """Prepare recommendation input only for an explicit command boundary."""

        return self._prepare_transition(
            canonical,
            evidence=evidence,
            target=OperationalTarget.RECOMMENDATION,
            transition="ANALYSIS_TO_RECOMMENDATION",
            actor_ref=actor_ref,
            command_id=command_id,
        )
