"""G7 transaction ownership contract.

The contract is declarative and infrastructure-free. Existing use cases remain
responsible for applying their current session policy until a later, separately
reviewed migration changes behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


TRANSACTION_CONTRACT_VERSION = "g7.own.02"


class TransactionOwner(StrEnum):
    APPLICATION_USE_CASE = "APPLICATION_USE_CASE"
    WORKER_USE_CASE = "WORKER_USE_CASE"


class TransactionScope(StrEnum):
    SHORT_COMMAND = "SHORT_COMMAND"
    AGGREGATE_OPERATION = "AGGREGATE_OPERATION"
    BATCH_ITEM = "BATCH_ITEM"
    DELIVERY_STATE_TRANSITION = "DELIVERY_STATE_TRANSITION"
    ACTION_APPLICATION = "ACTION_APPLICATION"


@dataclass(frozen=True)
class TransactionBoundary:
    """Explicit transaction policy for one application operation."""

    operation: str
    owner: TransactionOwner
    scope: TransactionScope
    commit_boundary: str
    rollback_boundary: str
    savepoint_policy: str
    retry_boundary: str
    failure_isolation: str
    recovery_semantics: str
    network_io_inside_transaction: bool = False


TRANSACTION_BOUNDARIES: tuple[TransactionBoundary, ...] = (
    TransactionBoundary(
        operation="WEB_COMMAND",
        owner=TransactionOwner.APPLICATION_USE_CASE,
        scope=TransactionScope.SHORT_COMMAND,
        commit_boundary="application command completion",
        rollback_boundary="command unit of work disposition",
        savepoint_policy="only for explicitly isolated sub-operation races",
        retry_boundary="same idempotency key and request hash",
        failure_isolation="one command does not corrupt another command",
        recovery_semantics="audit status or exception allows safe retry",
    ),
    TransactionBoundary(
        operation="HISTORICAL_MATERIALIZATION",
        owner=TransactionOwner.APPLICATION_USE_CASE,
        scope=TransactionScope.AGGREGATE_OPERATION,
        commit_boundary="one accepted draft materialization",
        rollback_boundary="materialization unit of work disposition",
        savepoint_policy="per uniqueness-sensitive child operation when required",
        retry_boundary="draft identity and materialization idempotency",
        failure_isolation="one draft failure does not erase caller transaction",
        recovery_semantics="caller decides retry or rejection after exception",
    ),
    TransactionBoundary(
        operation="REPLAY_RUN",
        owner=TransactionOwner.APPLICATION_USE_CASE,
        scope=TransactionScope.BATCH_ITEM,
        commit_boundary="bounded run or signal item, never the entire provider session",
        rollback_boundary="bounded item unit of work disposition",
        savepoint_policy="per evidence/event uniqueness race when required",
        retry_boundary="ReplayRun identity and provider request identity",
        failure_isolation="one signal/item failure is isolated where contract permits",
        recovery_semantics="failed run remains inspectable and retryable",
        network_io_inside_transaction=False,
    ),
    TransactionBoundary(
        operation="OUTBOX_DELIVERY",
        owner=TransactionOwner.WORKER_USE_CASE,
        scope=TransactionScope.DELIVERY_STATE_TRANSITION,
        commit_boundary="state transition before or after external delivery",
        rollback_boundary="one delivery state transition",
        savepoint_policy="not a substitute for delivery retry state",
        retry_boundary="delivery idempotency key and attempt",
        failure_isolation="one channel delivery does not abort the queue",
        recovery_semantics="RETRY or FAILED state with durable last error",
        network_io_inside_transaction=False,
    ),
    TransactionBoundary(
        operation="ALERT_ACTION",
        owner=TransactionOwner.WORKER_USE_CASE,
        scope=TransactionScope.ACTION_APPLICATION,
        commit_boundary="one lifecycle action application",
        rollback_boundary="one action use case",
        savepoint_policy="only inside the owning lifecycle operation",
        retry_boundary="lifecycle event identity",
        failure_isolation="one symbol/action failure does not stop other workers",
        recovery_semantics="durable lifecycle event or logged failure for recovery",
        network_io_inside_transaction=False,
    ),
    TransactionBoundary(
        operation="LIVE_EXECUTION",
        owner=TransactionOwner.APPLICATION_USE_CASE,
        scope=TransactionScope.AGGREGATE_OPERATION,
        commit_boundary="execution command state transition",
        rollback_boundary="execution command unit of work disposition",
        savepoint_policy="only for explicitly isolated event persistence",
        retry_boundary="exchange request identity and execution event identity",
        failure_isolation="exchange failure does not silently commit success",
        recovery_semantics="ORDER_PLACED or ORDER_FAILED is durable and auditable",
        network_io_inside_transaction=False,
    ),
)


def transaction_boundary_for(operation: str) -> TransactionBoundary:
    """Return the declared boundary for an operation."""

    normalized = operation.strip().upper()
    for boundary in TRANSACTION_BOUNDARIES:
        if boundary.operation == normalized:
            return boundary
    raise KeyError(operation)


def validate_transaction_boundaries() -> None:
    """Validate the invariants required by the G7 transaction contract."""

    operations = [boundary.operation for boundary in TRANSACTION_BOUNDARIES]
    if len(operations) != len(set(operations)):
        raise ValueError("Every operation must have one transaction boundary")
    if any(boundary.network_io_inside_transaction for boundary in TRANSACTION_BOUNDARIES):
        raise ValueError("Network I/O must not be inside a long-lived transaction contract")
    if any(not boundary.commit_boundary.strip() for boundary in TRANSACTION_BOUNDARIES):
        raise ValueError("Every operation must define a commit boundary")
    if any(not boundary.rollback_boundary.strip() for boundary in TRANSACTION_BOUNDARIES):
        raise ValueError("Every operation must define a rollback boundary")
