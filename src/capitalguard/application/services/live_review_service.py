from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LiveReviewPlan:
    mode: str
    recommended_route: str
    allowed_actions: tuple[str, ...]
    reason_codes: tuple[str, ...]
    creates_live_entity: bool = False


@dataclass(frozen=True)
class LiveReviewOutcome:
    action: str
    status: str
    creates_live_entity: bool
    message: str


class LiveReviewService:
    """Turns temporal decisions into explicit human-review actions only."""

    ACTIONS = {
        "LIVE_ELIGIBLE": ("ACCEPT_LIVE_REVIEW", "TRACK_ONLY", "IMPORT_HISTORICAL", "DISMISS"),
        "LIVE_STALE": ("RECOVER_REVIEW", "TRACK_ONLY", "IMPORT_HISTORICAL", "DISMISS"),
        "HISTORICAL_RECONSTRUCTION": ("IMPORT_HISTORICAL", "TRACK_ONLY", "DISMISS"),
        "CLOSED_EVENT": ("IMPORT_HISTORICAL", "TRACK_ONLY", "DISMISS"),
        "CONFLICT_REVIEW": ("REVIEW_CONFLICT", "DISMISS"),
    }

    def prepare(self, decision: dict[str, Any]) -> LiveReviewPlan:
        mode = str(decision.get("mode") or "CONFLICT_REVIEW").upper()
        actions = self.ACTIONS.get(mode, self.ACTIONS["CONFLICT_REVIEW"])
        route = str(decision.get("route") or "QUARANTINE").upper()
        reasons = tuple(str(item) for item in (decision.get("reason_codes") or []))
        return LiveReviewPlan(
            mode=mode,
            recommended_route=route,
            allowed_actions=actions,
            reason_codes=reasons,
            creates_live_entity=False,
        )

    def apply(self, plan: LiveReviewPlan, action: str) -> LiveReviewOutcome:
        normalized = action.strip().upper()
        if normalized not in plan.allowed_actions:
            raise ValueError(f"Action {normalized} is not allowed for {plan.mode}")
        if normalized == "DISMISS":
            return LiveReviewOutcome(normalized, "DISMISSED", False, "Review dismissed; no trading entity was created.")
        if normalized == "TRACK_ONLY":
            return LiveReviewOutcome(normalized, "TRACK_ONLY", False, "Source tracking recorded without activating a trader position.")
        if normalized == "IMPORT_HISTORICAL":
            return LiveReviewOutcome(normalized, "HISTORICAL_REVIEW_REQUESTED", False, "Historical import review requested; Replay and Owner Review remain required.")
        if normalized == "REVIEW_CONFLICT":
            return LiveReviewOutcome(normalized, "CONFLICT_REVIEW_REQUIRED", False, "Conflicting source/timeline data retained for owner review.")
        if normalized in {"ACCEPT_LIVE_REVIEW", "RECOVER_REVIEW"}:
            return LiveReviewOutcome(normalized, "LIVE_REVIEW_ACCEPTED", False, "Live review accepted; explicit Recommendation creation remains a separate gated action.")
        raise ValueError(f"Unsupported review action {normalized}")
