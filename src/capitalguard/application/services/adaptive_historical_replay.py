from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol


ANNUAL_CHUNK_DAYS = 365
MINUTES_PER_DAY = 24 * 60
PROVIDER_PAGE_LIMIT = 1000


@dataclass(frozen=True)
class ReplayWindow:
    start: datetime
    end: datetime
    limit: int


@dataclass(frozen=True)
class LifecycleState:
    activated: bool = False
    hit_target_indices: frozenset[int] = frozenset()
    remaining_target_indices: frozenset[int] = frozenset()
    current_stop: object | None = None
    lifecycle_state: str = "NOT_ACTIVATED"
    last_processed_timestamp: datetime | None = None


class AdaptiveReplayProvider(Protocol):
    def fetch_with_coverage(self, **kwargs): ...


class AdaptiveHistoricalReplayPlanner:
    """Pure planning layer for G6 annual macro traversal and 1m drill-down.

    It deliberately does not introduce a new persistence model or provider.
    Callers supply the existing provider and replay engine.
    """

    annual_chunk_days = ANNUAL_CHUNK_DAYS
    provider_page_limit = PROVIDER_PAGE_LIMIT

    @staticmethod
    def utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def day_start(cls, value: datetime) -> datetime:
        value = cls.utc(value)
        return value.replace(hour=0, minute=0, second=0, microsecond=0)

    @classmethod
    def annual_windows(cls, start: datetime, end: datetime) -> Iterable[ReplayWindow]:
        """Yield 365-day daily-macro windows, never extending past end."""
        cursor = cls.day_start(start)
        end = cls.utc(end)
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=cls.annual_chunk_days), end)
            days = max(1, (chunk_end - cursor).days + (1 if chunk_end.time() else 0))
            yield ReplayWindow(cursor, chunk_end, min(cls.annual_chunk_days, days))
            cursor = chunk_end

    @classmethod
    def minute_window_for_day(
        cls,
        *,
        day: datetime,
        signal_source_time: datetime,
        now: datetime,
    ) -> ReplayWindow | None:
        """Return only the requested portion of one day.

        Day 0 starts exactly at the signal timestamp. Later days start at 00:00.
        The end is clamped to the day boundary and to T_now. No next-day candles
        can be requested.
        """
        day_start = cls.day_start(day)
        day_end = day_start + timedelta(days=1)
        source = cls.utc(signal_source_time)
        now = cls.utc(now)
        start = max(day_start, source)
        end = min(day_end, now)
        if start >= end:
            return None
        minutes = int((end - start).total_seconds() // 60)
        if minutes <= 0:
            return None
        return ReplayWindow(start=start, end=end, limit=minutes)

    @classmethod
    def minute_page_windows(cls, window: ReplayWindow) -> Iterable[ReplayWindow]:
        """Split a bounded minute window into <=1000-candle requests."""
        cursor = cls.utc(window.start)
        end = cls.utc(window.end)
        while cursor < end:
            page_end = min(cursor + timedelta(minutes=cls.provider_page_limit), end)
            minutes = max(1, int((page_end - cursor).total_seconds() // 60))
            yield ReplayWindow(start=cursor, end=page_end, limit=min(cls.provider_page_limit, minutes))
            cursor = page_end

    @classmethod
    def page_count(cls, window: ReplayWindow) -> int:
        return sum(1 for _ in cls.minute_page_windows(window))

    @staticmethod
    def lifecycle_after_event(state: LifecycleState, *, event_type: str, target_index: int | None = None, stop=None, timestamp: datetime | None = None) -> LifecycleState:
        hit = set(state.hit_target_indices)
        remaining = set(state.remaining_target_indices)
        activated = state.activated
        lifecycle = state.lifecycle_state
        current_stop = state.current_stop if stop is None else stop
        if event_type == "ACTIVATED":
            activated = True
            lifecycle = "ACTIVE"
        elif event_type.startswith("TP") and target_index is not None:
            activated = True
            hit.add(target_index)
            remaining.discard(target_index)
            lifecycle = "CLOSED_TARGETS" if not remaining else "ACTIVE"
        elif event_type == "SL":
            lifecycle = "CLOSED_STOP"
        elif event_type == "CLOSE":
            lifecycle = "FINAL_CLOSE"
        elif event_type == "AMBIGUOUS":
            lifecycle = "CLOSED_UNVERIFIABLE"
        return LifecycleState(
            activated=activated,
            hit_target_indices=frozenset(hit),
            remaining_target_indices=frozenset(remaining),
            current_stop=current_stop,
            lifecycle_state=lifecycle,
            last_processed_timestamp=timestamp or state.last_processed_timestamp,
        )

    @staticmethod
    def terminal(state: LifecycleState) -> bool:
        return state.lifecycle_state in {"CLOSED_TARGETS", "CLOSED_STOP", "FINAL_CLOSE", "CLOSED_UNVERIFIABLE"}
