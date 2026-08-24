"""G7 command/event and side-effect ownership contract.

This module is declarative and does not persist events or dispatch side effects.
Existing event models and services remain the runtime source until a separately
reviewed behavioral migration is approved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


EVENT_CONTRACT_VERSION = "g7.own.03"


class EventOwner(StrEnum):
    LIFECYCLE_SERVICE = "LIFECYCLE_SERVICE"
    HISTORICAL_REPLAY_SERVICE = "HISTORICAL_REPLAY_SERVICE"
    PUBLICATION_OUTBOX_SERVICE = "PUBLICATION_OUTBOX_SERVICE"
    WEB_COMMAND_SERVICE = "WEB_COMMAND_SERVICE"
    AUTO_TRADE_SERVICE = "AUTO_TRADE_SERVICE"
    ALERT_SERVICE = "ALERT_SERVICE"


class EventAggregate(StrEnum):
    RECOMMENDATION = "RECOMMENDATION"
    USER_TRADE = "USER_TRADE"
    HISTORICAL_SIGNAL = "HISTORICAL_SIGNAL"
    REPLAY_RUN = "REPLAY_RUN"
    PUBLICATION_DELIVERY = "PUBLICATION_DELIVERY"
    COMMAND = "COMMAND"
    EXECUTION = "EXECUTION"


@dataclass(frozen=True)
class EventOwnership:
    """Ownership and effect boundary for one event family."""

    event_family: str
    aggregate: EventAggregate
    owner: EventOwner
    required_identity: str
    side_effect_policy: str
    projection_policy: str


EVENT_OWNERSHIP_CONTRACT: tuple[EventOwnership, ...] = (
    EventOwnership(
        event_family="RECOMMENDATION_LIFECYCLE",
        aggregate=EventAggregate.RECOMMENDATION,
        owner=EventOwner.LIFECYCLE_SERVICE,
        required_identity="recommendation_id + event_type + event_timestamp",
        side_effect_policy="state transition is owned by LifecycleService; notifications are downstream",
        projection_policy="performance/monitoring consumers are read-only",
    ),
    EventOwnership(
        event_family="USER_TRADE_LIFECYCLE",
        aggregate=EventAggregate.USER_TRADE,
        owner=EventOwner.LIFECYCLE_SERVICE,
        required_identity="user_trade_id + event_type + event_timestamp",
        side_effect_policy="UserTrade transition is owned by LifecycleService",
        projection_policy="performance consumes activated/closed history",
    ),
    EventOwnership(
        event_family="HISTORICAL_REPLAY",
        aggregate=EventAggregate.REPLAY_RUN,
        owner=EventOwner.HISTORICAL_REPLAY_SERVICE,
        required_identity="replay_run_id + event_type + event_timestamp",
        side_effect_policy="market evidence/event persistence only; no live action",
        projection_policy="outcome and trust read models consume verified events",
    ),
    EventOwnership(
        event_family="PUBLICATION_DELIVERY",
        aggregate=EventAggregate.PUBLICATION_DELIVERY,
        owner=EventOwner.PUBLICATION_OUTBOX_SERVICE,
        required_identity="delivery_id + operation + attempt",
        side_effect_policy="external Telegram delivery is retried by the outbox worker",
        projection_policy="operations feed is read-only",
    ),
    EventOwnership(
        event_family="WEB_COMMAND",
        aggregate=EventAggregate.COMMAND,
        owner=EventOwner.WEB_COMMAND_SERVICE,
        required_identity="idempotency_key + request_hash",
        side_effect_policy="command invokes the owning use case and records audit",
        projection_policy="command audit is not domain truth",
    ),
    EventOwnership(
        event_family="LIVE_EXECUTION",
        aggregate=EventAggregate.EXECUTION,
        owner=EventOwner.AUTO_TRADE_SERVICE,
        required_identity="execution command + exchange request identity",
        side_effect_policy="external exchange call is gated and produces durable success/failure",
        projection_policy="lifecycle and operations consume execution events",
    ),
    EventOwnership(
        event_family="MONITORING_ACTION",
        aggregate=EventAggregate.RECOMMENDATION,
        owner=EventOwner.ALERT_SERVICE,
        required_identity="recommendation_id + lifecycle event identity",
        side_effect_policy="AlertService routes actions to LifecycleService; StrategyEngine does not persist truth",
        projection_policy="monitoring telemetry is not a replacement for lifecycle events",
    ),
)


def event_ownership_for(event_family: str) -> EventOwnership:
    """Return the declared owner for an event family."""

    normalized = event_family.strip().upper()
    for entry in EVENT_OWNERSHIP_CONTRACT:
        if entry.event_family == normalized:
            return entry
    raise KeyError(event_family)


def validate_event_ownership_contract() -> None:
    """Validate event-family uniqueness and required ownership metadata."""

    families = [entry.event_family for entry in EVENT_OWNERSHIP_CONTRACT]
    if len(families) != len(set(families)):
        raise ValueError("Every event family must have one owner")
    for entry in EVENT_OWNERSHIP_CONTRACT:
        if not entry.required_identity.strip():
            raise ValueError(f"Missing event identity for {entry.event_family}")
        if not entry.side_effect_policy.strip():
            raise ValueError(f"Missing side-effect policy for {entry.event_family}")
        if not entry.projection_policy.strip():
            raise ValueError(f"Missing projection policy for {entry.event_family}")
