import pytest

from capitalguard.application.transaction_boundary import (
    TRANSACTION_BOUNDARIES,
    TransactionOwner,
    TransactionScope,
    transaction_boundary_for,
    validate_transaction_boundaries,
)


def test_transaction_boundaries_are_unique_and_valid():
    validate_transaction_boundaries()
    operations = [boundary.operation for boundary in TRANSACTION_BOUNDARIES]
    assert len(operations) == len(set(operations))
    assert all(boundary.commit_boundary for boundary in TRANSACTION_BOUNDARIES)
    assert all(boundary.rollback_boundary for boundary in TRANSACTION_BOUNDARIES)
    assert all(not boundary.network_io_inside_transaction for boundary in TRANSACTION_BOUNDARIES)


def test_replay_is_bounded_and_application_owned():
    boundary = transaction_boundary_for("replay_run")
    assert boundary.owner == TransactionOwner.APPLICATION_USE_CASE
    assert boundary.scope == TransactionScope.BATCH_ITEM
    assert "entire provider session" in boundary.commit_boundary
    assert boundary.network_io_inside_transaction is False


def test_outbox_and_alert_failures_are_isolated_per_unit():
    outbox = transaction_boundary_for("OUTBOX_DELIVERY")
    alert = transaction_boundary_for("ALERT_ACTION")
    assert "one delivery state transition" in outbox.rollback_boundary
    assert "one action use case" in alert.rollback_boundary
    assert "one channel delivery" in outbox.failure_isolation
    assert "one symbol/action" in alert.failure_isolation


def test_unknown_operation_is_rejected():
    with pytest.raises(KeyError):
        transaction_boundary_for("unknown")
