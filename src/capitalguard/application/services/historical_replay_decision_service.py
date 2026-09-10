# --- START OF FILE src/capitalguard/application/services/historical_replay_decision_service.py ---

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .historical_replay_version import REPLAY_POLICY_VERSION, REPLAY_VERSION

CURRENT_REPLAY_VERSION = REPLAY_VERSION
CURRENT_REPLAY_POLICY_VERSION = REPLAY_POLICY_VERSION


class ReplayAction(str, Enum):
    REUSE = "REUSE"
    REPROCESS = "REPROCESS"


class ReplayDecisionReason(str, Enum):
    NO_PREVIOUS_REPLAY = "NO_PREVIOUS_REPLAY"
    LEGACY_STILL_ACTIVE = "LEGACY_STILL_ACTIVE"
    FAILED = "FAILED"
    REPLAY_PARTIAL = "REPLAY_PARTIAL"
    STALE_ENGINE_VERSION = "STALE_ENGINE_VERSION"
    STALE_POLICY_VERSION = "STALE_POLICY_VERSION"
    INVALID_COVERAGE = "INVALID_COVERAGE"
    CURRENT_REPLAY_VALID = "CURRENT_REPLAY_VALID"


@dataclass(frozen=True)
class CoverageValidity:
    valid: bool
    reason: str
    requested_start: Any = None
    requested_end: Any = None
    actual_start: Any = None
    actual_end: Any = None
    gaps: tuple[Any, ...] = ()
    activation_risk: bool = False
    evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReplayDecision:
    action: ReplayAction
    reason: ReplayDecisionReason
    previous_run_id: int | None
    previous_status: str | None
    previous_replay_version: str | None
    previous_policy_version: str | None
    current_replay_version: str
    current_policy_version: str
    coverage: CoverageValidity

    @property
    def is_reprocess(self) -> bool:
        return self.action is ReplayAction.REPROCESS

    # ✅ CONSCIOUS ADDITION (D1): single-source structured audit contract.
    # Unavailable values are emitted as NULL (None), never fabricated.
    # Runtime callers (retry_g6) MUST consume this method, not build dicts.
    def audit_record(
        self,
        *,
        batch_id: int | None = None,
        receipt_id: int | None = None,
        signal_id: int | None = None,
        materialization_id: int | None = None,
        new_run_id: int | None = None,
        new_fingerprint: str | None = None,
        lineage_parent: int | None = None,
    ) -> dict[str, Any]:
        return {
            "Batch": batch_id,
            "Receipt": receipt_id,
            "Signal": signal_id,
            "Materialization": materialization_id,
            "Decision": self.action.value,
            "Reason": self.reason.value,
            "PreviousRun": self.previous_run_id,
            "EnginePrev": self.previous_replay_version,
            "EngineCurrent": self.current_replay_version,
            "PolicyPrev": self.previous_policy_version,
            "PolicyCurrent": self.current_policy_version,
            "Coverage": self.coverage.reason,
            "CoverageValid": self.coverage.valid,
            # REPROCESS-only fields: gated by is_reprocess → NULL on REUSE.
            "NewRun": new_run_id if self.is_reprocess else None,
            "LineageParent": lineage_parent if self.is_reprocess else None,
            # Fingerprint is meaningful for both REUSE and REPROCESS:
            # - REUSE: caller passes previous.request_fingerprint
            # - REPROCESS: caller passes new_run.request_fingerprint
            "Fingerprint": new_fingerprint,
        }


class HistoricalReplayDecisionAuthority:
    """Single, side-effect-free authority for REUSE versus REPROCESS."""

    def __init__(self, *, replay_version: str = CURRENT_REPLAY_VERSION, policy_version: str = CURRENT_REPLAY_POLICY_VERSION) -> None:
        self.replay_version = replay_version
        self.policy_version = policy_version

    @staticmethod
    def coverage_validity(run: Any | None) -> CoverageValidity:
        if run is None:
            return CoverageValidity(valid=False, reason="NO_REPLAY_EVIDENCE")
        result = dict(getattr(run, "result_json", None) or {})
        provider_meta = dict(getattr(run, "provider_metadata", None) or {})
        coverage = dict(result.get("coverage") or {})
        status = str(getattr(run, "coverage_status", None) or coverage.get("status") or "UNKNOWN").upper()
        gaps = tuple(coverage.get("gaps") or provider_meta.get("gaps") or ())
        activation_risk = bool(
            result.get("activation_risk")
            or result.get("entry_ambiguity")
            or result.get("coverage_activation_risk")
            or provider_meta.get("activation_risk")
            or provider_meta.get("entry_ambiguity")
        )
        evidence = {
            "coverage_status": status,
            "coverage_ratio": getattr(run, "coverage_ratio", None),
            "gaps": list(gaps),
            "activation_risk": activation_risk,
            "requested_start": coverage.get("requested_start") or provider_meta.get("requested_start"),
            "requested_end": coverage.get("requested_end") or provider_meta.get("requested_end"),
            "actual_start": coverage.get("actual_start") or provider_meta.get("actual_start"),
            "actual_end": coverage.get("actual_end") or provider_meta.get("actual_end"),
        }
        if activation_risk:
            return CoverageValidity(False, "GAP_CAN_HIDE_ACTIVATION", evidence=evidence, gaps=gaps, activation_risk=True, requested_start=evidence["requested_start"], requested_end=evidence["requested_end"], actual_start=evidence["actual_start"], actual_end=evidence["actual_end"])
        if status != "FULL":
            return CoverageValidity(False, f"COVERAGE_{status}", evidence=evidence, gaps=gaps, requested_start=evidence["requested_start"], requested_end=evidence["requested_end"], actual_start=evidence["actual_start"], actual_end=evidence["actual_end"])
        return CoverageValidity(True, "FULL_COVERAGE_NO_ACTIVATION_RISK", evidence=evidence, gaps=gaps, requested_start=evidence["requested_start"], requested_end=evidence["requested_end"], actual_start=evidence["actual_start"], actual_end=evidence["actual_end"])

    def decide(self, previous_run: Any | None) -> ReplayDecision:
        coverage = self.coverage_validity(previous_run)
        if previous_run is None:
            return ReplayDecision(ReplayAction.REPROCESS, ReplayDecisionReason.NO_PREVIOUS_REPLAY, None, None, None, None, self.replay_version, self.policy_version, coverage)
        status = str(getattr(previous_run, "status", "") or "").upper()
        previous_engine = getattr(previous_run, "replay_version", None)
        previous_policy = getattr(previous_run, "policy_version", None)
        if status == "STILL_ACTIVE":
            reason = ReplayDecisionReason.LEGACY_STILL_ACTIVE
        elif status == "FAILED":
            reason = ReplayDecisionReason.FAILED
        elif status == "REPLAY_PARTIAL":
            reason = ReplayDecisionReason.REPLAY_PARTIAL
        elif previous_engine != self.replay_version:
            reason = ReplayDecisionReason.STALE_ENGINE_VERSION
        elif previous_policy != self.policy_version:
            reason = ReplayDecisionReason.STALE_POLICY_VERSION
        elif not coverage.valid:
            reason = ReplayDecisionReason.INVALID_COVERAGE
        else:
            return ReplayDecision(ReplayAction.REUSE, ReplayDecisionReason.CURRENT_REPLAY_VALID, int(previous_run.id), status, previous_engine, previous_policy, self.replay_version, self.policy_version, coverage)
        return ReplayDecision(ReplayAction.REPROCESS, reason, int(previous_run.id), status, previous_engine, previous_policy, self.replay_version, self.policy_version, coverage)

    @staticmethod
    def assert_valid_lineage(*, previous_run: Any, new_run: Any) -> None:
        if previous_run is None:
            raise ValueError("A reprocess run requires a previous run")
        if int(previous_run.id) == int(new_run.id):
            raise ValueError("A reprocess must use a new ReplayRun id")
        if int(previous_run.signal_id) != int(new_run.signal_id):
            raise ValueError("Reprocessing must preserve signal identity")
        if int(previous_run.materialization_id) != int(new_run.materialization_id):
            raise ValueError("Reprocessing must preserve materialization identity")
        if getattr(previous_run, "request_fingerprint", None) == getattr(new_run, "request_fingerprint", None):
            raise ValueError("A reprocess must have a new unique request fingerprint")
        if int(getattr(new_run, "reprocess_of_run_id", -1)) != int(previous_run.id):
            raise ValueError("Invalid replay lineage parent")
        cursor = previous_run
        seen: set[int] = set()
        while cursor is not None:
            current_id = int(cursor.id)
            if current_id in seen or current_id == int(new_run.id):
                raise ValueError("Replay lineage cycle detected")
            seen.add(current_id)
            cursor = getattr(cursor, "reprocess_of", None)

# --- END OF FILE ---