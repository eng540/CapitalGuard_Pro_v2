from datetime import datetime, timedelta, timezone

import pytest

from capitalguard.domain.simulation_clock import SimulationClock


def test_simulation_clock_advances_monotonically():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = SimulationClock(start)
    next_time = start + timedelta(minutes=1)

    clock.advance_to(next_time)

    assert clock.current_time == next_time


def test_simulation_clock_rejects_backwards_movement():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = SimulationClock(start)

    with pytest.raises(ValueError, match="cannot move backwards"):
        clock.advance_to(start - timedelta(minutes=1))


def test_simulation_clock_requires_timezone_aware_initial_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        SimulationClock(datetime(2026, 1, 1))
