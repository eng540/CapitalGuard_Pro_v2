"""Safety policy for a replay-to-live continuation decision.

The gate is intentionally side-effect free. It does not create a live entity,
subscribe to a price stream, mutate a lifecycle, or write audit records. A
caller must persist the decision and perform the separately authorized command
through existing Core boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from capitalguard.domain.protection_policy import ProtectionPolicy


class HandoffStatus(str, Enum):
    APPROVED = "HANDOFF_APPROVED"
    BLOCKED = "HANDOFF_BLOCKED"


@dataclass(frozen=True)
class ContinuumHandoffFacts:
    """Facts collected by existing Core services before a handoff decision."""

    parse_complete: bool
    source_trusted: bool
    replay_evidence_verified: bool
    lifecycle_status: str
    duplicate_exists: bool
    protection_policy: ProtectionPolicy | Mapping[str, Any] | None
    consent_given: bool
    idempotency_key: str | None
    audit_ready: bool = True


@dataclass(frozen=True)
class ContinuumHandoffDecision:
    status: HandoffStatus
    reason_codes: tuple[str, ...]
    approved: bool

    @property
    def blocked(self) -> bool:
        return not self.approved


class ContinuumHandoffGate:
    """Evaluate all safety prerequisites without performing the handoff."""

    ACTIVE_STATUSES = {"ACTIVE", "PENDING_ENTRY"}

    @staticmethod
    def _valid_protection_policy(policy: ProtectionPolicy | Mapping[str, Any] | None) -> bool:
        if policy is None:
            return False
        if isinstance(policy, ProtectionPolicy):
            return policy.is_valid()
        if isinstance(policy, Mapping):
            try:
                return ProtectionPolicy.from_record(dict(policy)).is_valid()
            except (TypeError, ValueError):
                return False
        return False

    def evaluate(self, facts: ContinuumHandoffFacts) -> ContinuumHandoffDecision:
        reasons: list[str] = []
        if not facts.parse_complete:
            reasons.append("PARSE_INCOMPLETE")
        if not facts.source_trusted:
            reasons.append("SOURCE_NOT_TRUSTED")
        if not facts.replay_evidence_verified:
            reasons.append("REPLAY_EVIDENCE_UNVERIFIED")
        if str(facts.lifecycle_status or "").upper() not in self.ACTIVE_STATUSES:
            reasons.append("LIFECYCLE_NOT_ACTIVE")
        if facts.duplicate_exists:
            reasons.append("LIVE_ENTITY_ALREADY_EXISTS")
        if not self._valid_protection_policy(facts.protection_policy):
            reasons.append("PROTECTION_POLICY_INVALID_OR_MISSING")
        if not facts.consent_given:
            reasons.append("EXPLICIT_CONSENT_REQUIRED")
        if not str(facts.idempotency_key or "").strip():
            reasons.append("IDEMPOTENCY_KEY_REQUIRED")
        if not facts.audit_ready:
            reasons.append("AUDIT_CONTEXT_NOT_READY")
        if reasons:
            return ContinuumHandoffDecision(
                status=HandoffStatus.BLOCKED,
                reason_codes=tuple(reasons),
                approved=False,
            )
        return ContinuumHandoffDecision(
            status=HandoffStatus.APPROVED,
            reason_codes=(),
            approved=True,
        )


__all__ = [
    "ContinuumHandoffDecision",
    "ContinuumHandoffFacts",
    "ContinuumHandoffGate",
    "HandoffStatus",
]
