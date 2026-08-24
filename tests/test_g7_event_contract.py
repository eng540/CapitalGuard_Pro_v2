import pytest

from capitalguard.application.event_contract import (
    EVENT_OWNERSHIP_CONTRACT,
    EventAggregate,
    EventOwner,
    event_ownership_for,
    validate_event_ownership_contract,
)


def test_event_ownership_contract_is_unique_and_complete():
    validate_event_ownership_contract()
    families = [entry.event_family for entry in EVENT_OWNERSHIP_CONTRACT]
    assert len(families) == len(set(families))
    assert all(entry.required_identity for entry in EVENT_OWNERSHIP_CONTRACT)
    assert all(entry.side_effect_policy for entry in EVENT_OWNERSHIP_CONTRACT)
    assert all(entry.projection_policy for entry in EVENT_OWNERSHIP_CONTRACT)


def test_lifecycle_and_replay_have_distinct_event_owners():
    lifecycle = event_ownership_for("recommendation_lifecycle")
    replay = event_ownership_for("historical_replay")
    assert lifecycle.owner == EventOwner.LIFECYCLE_SERVICE
    assert lifecycle.aggregate == EventAggregate.RECOMMENDATION
    assert replay.owner == EventOwner.HISTORICAL_REPLAY_SERVICE
    assert replay.aggregate == EventAggregate.REPLAY_RUN
    assert lifecycle.owner != replay.owner


def test_read_models_and_monitoring_cannot_replace_domain_truth():
    monitoring = event_ownership_for("monitoring_action")
    command = event_ownership_for("web_command")
    assert "does not persist truth" in monitoring.side_effect_policy.lower()
    assert "not domain truth" in command.projection_policy.lower()


def test_unknown_event_family_is_rejected():
    with pytest.raises(KeyError):
        event_ownership_for("unknown")
