from datetime import datetime, timezone

import pytest

from capitalguard.application.services.job_lifecycle import JobExecution, JobState


BASE = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def test_job_lifecycle_tracks_attempts_and_bounded_backoff():
    job = JobExecution("replay-1", max_attempts=3)
    job.start(now=BASE)
    job.fail("provider unavailable", now=BASE, retry_delay_seconds=10)

    assert job.state == JobState.FAILED
    assert job.attempt == 1
    assert job.retry_at == BASE.replace(second=10)

    job.start(now=BASE.replace(second=20))
    job.fail("provider still unavailable", now=BASE.replace(second=20), retry_delay_seconds=10)
    assert job.retry_at == BASE.replace(second=40)

    job.start(now=BASE.replace(second=50))
    job.succeed(now=BASE.replace(second=55))
    assert job.state == JobState.SUCCEEDED
    assert job.finished_at == BASE.replace(second=55)


def test_job_lifecycle_rejects_restart_after_retry_budget():
    job = JobExecution("replay-2", max_attempts=1)
    job.start(now=BASE)
    job.fail("permanent failure", now=BASE)

    with pytest.raises(ValueError, match="retry budget exhausted"):
        job.start(now=BASE)


def test_job_lifecycle_cancel_is_terminal_and_idempotent():
    job = JobExecution("replay-3")
    job.cancel(now=BASE)
    job.cancel(now=BASE)

    assert job.state == JobState.CANCELLED
    assert job.retry_at is None
