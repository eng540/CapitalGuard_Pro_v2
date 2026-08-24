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


class OperationalDecisionError(ValueError):
    """Raised when canonical input cannot cross the decision boundary."""


@dataclass(frozen=True)
class OperationalDecision:
    """Traceable decision handoff with no persistence or external side effect."""

    status: str
    target: OperationalTarget
    input_state: DecisionInputState
    decision_fingerprint: str
    canonical: dict[str, Any]
    evidence: dict[str, Any]
    execution_allowed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target.value,
            "input_state": self.input_state.value,
            "decision_fingerprint": self.decision_fingerprint,
            "canonical": self.canonical,
            "evidence": self.evidence,
            "execution_allowed": self.execution_allowed,
        }


class OperationalDecisionService:
    """Prepare a canonical semantic projection for a domain decision use case."""

    REQUIRED_FIELDS = ("asset", "direction", "entry", "stop_loss", "targets", "market")

    @staticmethod
    def _decimal(value: Any, field: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise OperationalDecisionError(f"{field} must be a finite positive number") from exc
        if not number.is_finite() or number <= 0:
            raise OperationalDecisionError(f"{field} must be a finite positive number")
        return number

    @classmethod
    def _normalize_canonical(cls, canonical: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(canonical)
        missing = [field for field in cls.REQUIRED_FIELDS if not normalized.get(field)]
        if missing:
            raise OperationalDecisionError(f"canonical input is incomplete: {', '.join(missing)}")
        normalized["asset"] = str(normalized["asset"]).strip().upper()
        normalized["direction"] = str(normalized["direction"]).strip().upper()
        normalized["market"] = str(normalized["market"]).strip().upper()
        if normalized["direction"] not in {"LONG", "SHORT"}:
            raise OperationalDecisionError("direction must be LONG or SHORT")
        if normalized["market"] not in {"SPOT", "FUTURES"}:
            raise OperationalDecisionError("market must be SPOT or FUTURES")
        normalized["entry"] = str(cls._decimal(normalized["entry"], "entry"))
        normalized["stop_loss"] = str(cls._decimal(normalized["stop_loss"], "stop_loss"))
        targets = normalized["targets"]
        if not isinstance(targets, (list, tuple)) or not targets:
            raise OperationalDecisionError("targets must be a non-empty list")
        normalized_targets: list[str] = []
        for index, target in enumerate(targets, start=1):
            value = target.get("value") if isinstance(target, Mapping) else target
            normalized_targets.append(str(cls._decimal(value, f"target[{index}]")))
        normalized["targets"] = normalized_targets
        return normalized

    @staticmethod
    def _fingerprint(canonical: Mapping[str, Any], evidence: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            {"canonical": canonical, "evidence": evidence},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def prepare(
        self,
        canonical: Mapping[str, Any],
        *,
        evidence: Mapping[str, Any] | None = None,
        target: OperationalTarget = OperationalTarget.ANALYTICAL_RESULT,
    ) -> OperationalDecision:
        """Validate accepted canonical input without persistence or side effects."""

        if target == OperationalTarget.EXECUTION_STATE:
            raise OperationalDecisionError("execution requires the explicit execution command boundary")
        decision_boundary_for("canonical_to_analysis")
        normalized = self._normalize_canonical(canonical)
        evidence_payload = dict(evidence or {})
        fingerprint = self._fingerprint(normalized, evidence_payload)
        return OperationalDecision(
            status="READY_FOR_ANALYSIS",
            target=target,
            input_state=DecisionInputState.CANONICAL_ACCEPTED,
            decision_fingerprint=fingerprint,
            canonical=normalized,
            evidence=evidence_payload,
            execution_allowed=False,
        )
