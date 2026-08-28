from __future__ import annotations

from datetime import datetime


class SimulationClock:
    """Monotonic clock used only by deterministic historical simulation."""

    def __init__(self, initial_time: datetime):
        if initial_time.tzinfo is None:
            raise ValueError("Simulation clock requires a timezone-aware datetime")
        self._current_time = initial_time

    @property
    def current_time(self) -> datetime:
        return self._current_time

    def advance_to(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            raise ValueError("Simulation clock requires a timezone-aware datetime")
        if new_time < self._current_time:
            raise ValueError("Simulation clock cannot move backwards")
        self._current_time = new_time
