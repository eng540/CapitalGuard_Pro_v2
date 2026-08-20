from __future__ import annotations

from decimal import Decimal
from typing import Any

from capitalguard.application.services.price_validity_service import PriceValidityService
from capitalguard.domain.temporal import (
    PriceValidity,
    TemporalContext,
    TemporalDecision,
    TemporalMode,
    TimelineRelation,
    decimal,
)


class TemporalDecisionService:
    """Makes one auditable routing decision from temporal and market facts."""

    def __init__(
        self,
        price_validity_service: PriceValidityService | None = None,
        *,
        live_max_age_seconds: int = 180,
        historical_transition_seconds: int = 3600,
    ):
        if live_max_age_seconds <= 0:
            raise ValueError("live_max_age_seconds must be positive")
        if historical_transition_seconds < live_max_age_seconds:
            raise ValueError("historical_transition_seconds must be >= live_max_age_seconds")
        self.price_validity_service = price_validity_service or PriceValidityService()
        self.live_max_age_seconds = live_max_age_seconds
        self.historical_transition_seconds = historical_transition_seconds

    @staticmethod
    def _relation(value: Any) -> TimelineRelation:
        if isinstance(value, TimelineRelation):
            return value
        normalized = str(value or "UNRELATED").strip().upper()
        try:
            return TimelineRelation(normalized)
        except ValueError:
            return TimelineRelation.UNRELATED

    @staticmethod
    def _payload_complete(payload: dict[str, Any] | None) -> bool:
        if not payload:
            return False
        return bool(
            payload.get("asset")
            and payload.get("side")
            and decimal(payload.get("entry")) is not None
            and decimal(payload.get("stop_loss")) is not None
            and payload.get("targets")
        )

    @staticmethod
    def _replay_readiness(
        temporal: TemporalContext,
        payload_complete: bool,
        market_data_available: bool,
    ) -> Decimal:
        score = Decimal("0")
        if temporal.has_verified_origin:
            score += Decimal("0.30")
        if temporal.effective_market_as_of is not None:
            score += Decimal("0.20")
        if payload_complete:
            score += Decimal("0.30")
        if market_data_available:
            score += Decimal("0.20")
        return min(Decimal("1"), score)

    def decide(
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
    ) -> TemporalDecision:
        relation = self._relation(timeline_relation)
        payload = parsed_payload or {}
        complete = self._payload_complete(payload)
        terminal = relation in {TimelineRelation.CLOSE, TimelineRelation.TARGET_HIT} or str(event_kind or "").upper() in {
            "CLOSE",
            "CLOSED",
            "FINAL_CLOSE",
            "TARGET_HIT",
            "SL",
        }
        update = relation not in {TimelineRelation.UNRELATED, TimelineRelation.INITIAL_SIGNAL} or str(event_kind or "").upper() in {
            "UPDATE",
            "AMENDMENT",
            "STOP_UPDATE",
            "TARGET_UPDATE",
            "PARTIAL_EXIT",
        }
        if timeline_conflict:
            return TemporalDecision(
                mode=TemporalMode.CONFLICT_REVIEW,
                confidence=Decimal("0.35"),
                reason_codes=("TIMELINE_PARENT_CONFLICT", "MANUAL_RECONCILIATION_REQUIRED"),
                temporal=temporal,
                timeline_relation=relation,
                replay_readiness=self._replay_readiness(temporal, complete, market_data_available),
                requires_review=True,
            )

        if duplicate:
            return TemporalDecision(
                mode=TemporalMode.DUPLICATE,
                confidence=Decimal("1"),
                reason_codes=("SOURCE_REVISION_ALREADY_SEEN",),
                temporal=temporal,
                timeline_relation=relation,
                replay_readiness=Decimal("1" if complete else "0.5"),
                requires_review=False,
            )

        if not temporal.has_verified_origin:
            return TemporalDecision(
                mode=TemporalMode.UNVERIFIED_TIME,
                confidence=Decimal("0.20"),
                reason_codes=("SOURCE_ORIGIN_UNVERIFIED", "HISTORICAL_ONLY"),
                temporal=temporal,
                timeline_relation=relation,
                replay_readiness=self._replay_readiness(temporal, complete, market_data_available),
                requires_review=True,
            )

        if edited_after_market or (
            temporal.edit_time is not None
            and temporal.source_time is not None
            and temporal.edit_time > temporal.source_time
            and temporal.event_time is not None
            and temporal.edit_time > temporal.event_time
        ):
            return TemporalDecision(
                mode=TemporalMode.EDITED_AFTER_MARKET,
                confidence=Decimal("0.85"),
                reason_codes=("EDIT_AFTER_EVENT_TIME", "NEW_REVISION_REQUIRED"),
                temporal=temporal,
                timeline_relation=relation,
                replay_readiness=self._replay_readiness(temporal, complete, market_data_available),
                requires_review=True,
            )

        if update:
            mode = TemporalMode.CLOSED_EVENT if terminal else TemporalMode.UPDATE_EVENT
            reason = "TERMINAL_EVENT" if terminal else "TIMELINE_UPDATE"
            return TemporalDecision(
                mode=mode,
                confidence=Decimal("0.85"),
                reason_codes=(reason, "APPEND_ONLY_TIMELINE"),
                temporal=temporal,
                timeline_relation=relation,
                replay_readiness=self._replay_readiness(temporal, complete, market_data_available),
                requires_review=terminal,
            )

        validity: PriceValidity = self.price_validity_service.evaluate(
            source_time=temporal.source_time,
            received_time=temporal.received_time,
            current_price=current_price,
            reference_price=payload.get("entry"),
            stop_loss=payload.get("stop_loss"),
            max_age_seconds=self.live_max_age_seconds,
            market_data_available=market_data_available,
            max_drift_pct=max_drift_pct,
        )
        age = temporal.age_seconds
        readiness = self._replay_readiness(temporal, complete, market_data_available)
        if age is None or age >= self.historical_transition_seconds:
            mode = TemporalMode.HISTORICAL_RECONSTRUCTION
            reasons = ("SOURCE_AGE_EXCEEDS_HISTORICAL_TRANSITION", "REPLAY_REQUIRED")
            confidence = Decimal("0.88")
        elif validity.valid_for_live and complete:
            mode = TemporalMode.LIVE_ELIGIBLE
            reasons = ("FRESH_SOURCE_WITHIN_ENTRY_ENVELOPE", "LIVE_REVIEW_REQUIRED")
            confidence = Decimal("0.75") + (validity.score * Decimal("0.25"))
        else:
            mode = TemporalMode.LIVE_STALE
            reasons = tuple(
                [
                    "SOURCE_AGE_EXCEEDS_LIVE_WINDOW"
                    if age is not None and age > self.live_max_age_seconds
                    else "LIVE_PRICE_NOT_VALID",
                    "STALE_LIVE_CANDIDATE",
                ]
                + list(validity.reason_codes)
            )
            confidence = Decimal("0.70")

        return TemporalDecision(
            mode=mode,
            confidence=min(Decimal("1"), confidence),
            reason_codes=reasons,
            temporal=temporal,
            price_validity=validity,
            timeline_relation=relation,
            replay_readiness=readiness,
            requires_review=mode in {TemporalMode.LIVE_ELIGIBLE, TemporalMode.LIVE_STALE},
        )
