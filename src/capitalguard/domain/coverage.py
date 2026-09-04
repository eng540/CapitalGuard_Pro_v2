from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable


class CoverageStatus(str, Enum):
    FULL = "FULL"
    PARTIAL_WINDOW = "PARTIAL_WINDOW"
    GAPPED = "GAPPED"
    UNAVAILABLE = "UNAVAILABLE"


INTERVAL_DELTAS = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
}


@dataclass(frozen=True)
class HistoricalCoverage:
    requested_start: datetime
    requested_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    expected_candles: int
    actual_candles: int
    coverage_ratio: float
    status: CoverageStatus
    gaps: tuple[tuple[datetime, datetime], ...] = ()

    @property
    def is_complete(self) -> bool:
        return self.status is CoverageStatus.FULL and self.coverage_ratio >= 0.999

    @property
    def missing_candles(self) -> int:
        return max(0, self.expected_candles - self.actual_candles)


def interval_delta(interval: str) -> timedelta:
    try:
        return INTERVAL_DELTAS[interval.strip()]
    except KeyError as exc:
        raise ValueError(f"Unsupported historical interval: {interval}") from exc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Historical coverage timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _floor_to_grid(value: datetime, interval: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    normalized = _utc(value)
    return epoch + ((normalized - epoch) // interval) * interval


def _expected_market_grid(*, requested_start: datetime, requested_end: datetime, interval: timedelta) -> tuple[datetime, datetime, list[datetime]]:
    start = _utc(requested_start)
    end = _utc(requested_end)
    market_grid_start = _floor_to_grid(start, interval) + interval
    duration = end - start
    expected_count = int((duration + interval - timedelta(microseconds=1)) // interval)
    expected_count = max(0, expected_count)
    market_grid_end = (
        market_grid_start + interval * (expected_count - 1)
        if expected_count
        else market_grid_start - interval
    )
    expected = [market_grid_start + interval * index for index in range(expected_count)]
    return market_grid_start, market_grid_end, expected


def calculate_historical_coverage(*, requested_start: datetime, requested_end: datetime, candle_times: Iterable[datetime], interval: timedelta) -> HistoricalCoverage:
    start = _utc(requested_start)
    end = _utc(requested_end)
    if start >= end:
        raise ValueError("Historical coverage bounds are invalid")
    if interval.total_seconds() <= 0:
        raise ValueError("Historical coverage interval must be positive")

    market_grid_start, market_grid_end, expected_times = _expected_market_grid(
        requested_start=start, requested_end=end, interval=interval
    )
    expected_set = set(expected_times)
    expected = len(expected_times)
    normalized_times = {_utc(item) for item in candle_times if start <= _utc(item) <= end}
    observed = sorted(normalized_times)
    if not observed:
        return HistoricalCoverage(start, end, None, None, expected, 0, 0.0, CoverageStatus.UNAVAILABLE)

    actual_start, actual_end = observed[0], observed[-1]
    actual = len(observed)
    missing = sorted(expected_set - normalized_times)
    gaps: list[tuple[datetime, datetime]] = []
    if missing:
        gap_start = previous = missing[0]
        for timestamp in missing[1:]:
            if timestamp != previous + interval:
                gaps.append((gap_start, previous + interval))
                gap_start = timestamp
            previous = timestamp
        gaps.append((gap_start, previous + interval))

    # A provider may also return the source candle itself. That candle is kept
    # as evidence, but completeness is judged on the strict post-source grid.
    boundaries_complete = (
        bool(expected_times)
        and actual_start <= market_grid_start
        and actual_end >= market_grid_end
    )
    if boundaries_complete and missing:
        status = CoverageStatus.GAPPED
    elif boundaries_complete:
        status = CoverageStatus.FULL
    else:
        status = CoverageStatus.PARTIAL_WINDOW

    ratio = min(1.0, actual / expected) if expected else 0.0
    return HistoricalCoverage(
        requested_start=start,
        requested_end=end,
        actual_start=actual_start,
        actual_end=actual_end,
        expected_candles=expected,
        actual_candles=actual,
        coverage_ratio=ratio,
        status=status,
        gaps=tuple(gaps),
    )
