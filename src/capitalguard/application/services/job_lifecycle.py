from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum


class JobState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class JobExecution:
    """In-memory lifecycle contract shared by long-running service adapters.

    Persistence remains owned by the existing batch/outbox models. This object
    centralizes legal state transitions and bounded retry timing without adding
    another database table or worker implementation.
    """

    job_id: str
    state: JobState = JobState.PENDING
    attempt: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_at: datetime | None = None

    def start(self, *, now: datetime | None = None) -> None:
        if self.state not in {JobState.PENDING, JobState.FAILED}:
            raise ValueError(f"Cannot start job from {self.state}")
        if self.attempt >= self.max_attempts:
            raise ValueError("Job retry budget exhausted")
        timestamp = now or datetime.now(timezone.utc)
        self.attempt += 1
        self.state = JobState.RUNNING
        self.started_at = timestamp
        self.finished_at = None
        self.retry_at = None
        self.last_error = None

    def succeed(self, *, now: datetime | None = None) -> None:
        self._require_running()
        self.state = JobState.SUCCEEDED
        self.finished_at = now or datetime.now(timezone.utc)

    def fail(self, error: str, *, now: datetime | None = None, retry_delay_seconds: int = 30) -> None:
        self._require_running()
        self.state = JobState.FAILED
        self.last_error = str(error)[:1000]
        self.finished_at = now or datetime.now(timezone.utc)
        if self.attempt < self.max_attempts:
            delay = max(1, int(retry_delay_seconds)) * (2 ** max(0, self.attempt - 1))
            self.retry_at = self.finished_at + timedelta(seconds=delay)

    def cancel(self, *, now: datetime | None = None) -> None:
        if self.state in {JobState.SUCCEEDED, JobState.CANCELLED}:
            return
        self.state = JobState.CANCELLED
        self.finished_at = now or datetime.now(timezone.utc)
        self.retry_at = None

    def _require_running(self) -> None:
        if self.state != JobState.RUNNING:
            raise ValueError(f"Job must be RUNNING, got {self.state}")
