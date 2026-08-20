from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from capitalguard.domain.temporal import TemporalContext, TemporalDecision, TemporalMode, TimelineRelation

from .temporal_decision_service import TemporalDecisionService


class ApplicationRoute(str, Enum):
    LIVE_REVIEW = "LIVE_REVIEW"
    HISTORICAL_CANDIDATE = "HISTORICAL_CANDIDATE"
    TIMELINE_EVENT = "TIMELINE_EVENT"
    CLOSED_EVENT = "CLOSED_EVENT"
    REVISION_REVIEW = "REVISION_REVIEW"
    QUARANTINE = "QUARANTINE"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class ForwardRoutePlan:
    route: ApplicationRoute
    decision: TemporalDecision
    creates_live_entity: bool
    creates_historical_candidate: bool
    appends_timeline_event: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "creates_live_entity": self.creates_live_entity,
            "creates_historical_candidate": self.creates_historical_candidate,
            "appends_timeline_event": self.appends_timeline_event,
            "decision": self.decision.as_dict(),
        }


class ForwardIntakeRouter:
    """Single decision boundary for live, historical, and timeline forwarding."""

    def __init__(self, decision_service: TemporalDecisionService | None = None):
        self.decision_service = decision_service or TemporalDecisionService()

    @staticmethod
    def _route_for(mode: TemporalMode) -> ApplicationRoute:
        return {
            TemporalMode.LIVE_ELIGIBLE: ApplicationRoute.LIVE_REVIEW,
            TemporalMode.LIVE_STALE: ApplicationRoute.HISTORICAL_CANDIDATE,
            TemporalMode.HISTORICAL_RECONSTRUCTION: ApplicationRoute.HISTORICAL_CANDIDATE,
            TemporalMode.UPDATE_EVENT: ApplicationRoute.TIMELINE_EVENT,
            TemporalMode.CLOSED_EVENT: ApplicationRoute.CLOSED_EVENT,
            TemporalMode.EDITED_AFTER_MARKET: ApplicationRoute.REVISION_REVIEW,
            TemporalMode.UNVERIFIED_TIME: ApplicationRoute.QUARANTINE,
            TemporalMode.CONFLICT_REVIEW: ApplicationRoute.REVISION_REVIEW,
            TemporalMode.DUPLICATE: ApplicationRoute.DUPLICATE,
        }[mode]

    def plan(
        self,
        *,
        temporal: TemporalContext,
        parsed_payload: dict[str, Any] | None = None,
        current_price: Any = None,
        event_kind: str | None = None,
        timeline_relation: TimelineRelation | str = TimelineRelation.UNRELATED,
        duplicate: bool = False,
        market_data_available: bool = True,
        edited_after_market: bool = False,
        timeline_conflict: bool = False,
        max_drift_pct: Any = None,
    ) -> ForwardRoutePlan:
        decision = self.decision_service.decide(
            temporal=temporal,
            parsed_payload=parsed_payload,
            current_price=current_price,
            event_kind=event_kind,
            timeline_relation=timeline_relation,
            duplicate=duplicate,
            market_data_available=market_data_available,
            edited_after_market=edited_after_market,
            timeline_conflict=timeline_conflict,
            max_drift_pct=max_drift_pct,
        )
        route = self._route_for(decision.mode)
        return ForwardRoutePlan(
            route=route,
            decision=decision,
            creates_live_entity=route == ApplicationRoute.LIVE_REVIEW,
            creates_historical_candidate=route
            in {
                ApplicationRoute.HISTORICAL_CANDIDATE,
                ApplicationRoute.QUARANTINE,
                ApplicationRoute.REVISION_REVIEW,
            },
            appends_timeline_event=route
            in {
                ApplicationRoute.TIMELINE_EVENT,
                ApplicationRoute.CLOSED_EVENT,
                ApplicationRoute.REVISION_REVIEW,
            },
        )
