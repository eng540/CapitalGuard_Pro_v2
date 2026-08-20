from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping


class TemporalMode(str, Enum):
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
    LIVE_STALE = "LIVE_STALE"
    HISTORICAL_RECONSTRUCTION = "HISTORICAL_RECONSTRUCTION"
    UPDATE_EVENT = "UPDATE_EVENT"
    CLOSED_EVENT = "CLOSED_EVENT"
    EDITED_AFTER_MARKET = "EDITED_AFTER_MARKET"
    UNVERIFIED_TIME = "UNVERIFIED_TIME"
    DUPLICATE = "DUPLICATE"
    CONFLICT_REVIEW = "CONFLICT_REVIEW"


class TimelineRelation(str, Enum):
    INITIAL_SIGNAL = "INITIAL_SIGNAL"
    AMENDMENT = "AMENDMENT"
    ENTRY_UPDATE = "ENTRY_UPDATE"
    STOP_UPDATE = "STOP_UPDATE"
    TARGET_UPDATE = "TARGET_UPDATE"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    TARGET_HIT = "TARGET_HIT"
    CLOSE = "CLOSE"
    UNRELATED = "UNRELATED"


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


@dataclass(frozen=True)
class TemporalContext:
    source_time: datetime | None
    received_time: datetime
    ingested_time: datetime
    event_time: datetime | None = None
    edit_time: datetime | None = None
    source_origin_type: str | None = None
    source_chat_id: int | None = None
    source_message_id: int | None = None
    source_message_revision: int = 0
    source_time_verified: bool = False

    def __post_init__(self) -> None:
        received = utc(self.received_time)
        ingested = utc(self.ingested_time)
        if received is None or ingested is None:
            raise ValueError("received_time and ingested_time are required")
        if self.source_message_revision < 0:
            raise ValueError("source_message_revision cannot be negative")
        for name in ("source_time", "event_time", "edit_time"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        object.__setattr__(self, "received_time", received)
        object.__setattr__(self, "ingested_time", ingested)
        if self.source_time and self.source_time > self.received_time:
            raise ValueError("source_time cannot be after received_time")
        if self.event_time and self.event_time > self.received_time:
            raise ValueError("event_time cannot be after received_time")

    @property
    def age_seconds(self) -> int | None:
        if self.source_time is None:
            return None
        return max(0, int((self.received_time - self.source_time).total_seconds()))

    @property
    def effective_market_as_of(self) -> datetime | None:
        return self.event_time or self.source_time

    @property
    def has_verified_origin(self) -> bool:
        return bool(self.source_time_verified and self.source_time and self.source_origin_type == "CHANNEL")


@dataclass(frozen=True)
class PriceValidity:
    score: Decimal
    freshness_score: Decimal
    distance_score: Decimal
    market_data_quality_score: Decimal
    valid_for_live: bool
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    current_price: Decimal | None = None
    reference_price: Decimal | None = None
    drift_pct: Decimal | None = None
    max_drift_pct: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("score", "freshness_score", "distance_score", "market_data_quality_score"):
            value = decimal(getattr(self, name))
            if value is None or value < 0 or value > 1:
                raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value.quantize(Decimal("0.0001")))


@dataclass(frozen=True)
class TemporalDecision:
    mode: TemporalMode
    confidence: Decimal
    reason_codes: tuple[str, ...]
    temporal: TemporalContext
    price_validity: PriceValidity | None = None
    timeline_relation: TimelineRelation = TimelineRelation.UNRELATED
    replay_readiness: Decimal = Decimal("0")
    requires_review: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        confidence = decimal(self.confidence)
        readiness = decimal(self.replay_readiness)
        if confidence is None or confidence < 0 or confidence > 1:
            raise ValueError("confidence must be between 0 and 1")
        if readiness is None or readiness < 0 or readiness > 1:
            raise ValueError("replay_readiness must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence.quantize(Decimal("0.0001")))
        object.__setattr__(self, "replay_readiness", readiness.quantize(Decimal("0.0001")))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "confidence": str(self.confidence),
            "reason_codes": list(self.reason_codes),
            "age_seconds": self.temporal.age_seconds,
            "market_as_of": self.temporal.effective_market_as_of.isoformat()
            if self.temporal.effective_market_as_of
            else None,
            "price_validity": str(self.price_validity.score) if self.price_validity else None,
            "timeline_relation": self.timeline_relation.value,
            "replay_readiness": str(self.replay_readiness),
            "requires_review": self.requires_review,
            "metadata": dict(self.metadata),
        }
