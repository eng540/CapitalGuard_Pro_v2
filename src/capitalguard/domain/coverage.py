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
    "1m": timedelta(minutes=1), "3m": timedelta(minutes=3), "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15), "30m": timedelta(minutes=30), "1h": timedelta(hours=1),
    "2h": timedelta(hours=2), "4h": timedelta(hours=4), "6h": timedelta(hours=6),
    "8h": timedelta(hours=8), "12h": timedelta(hours=12), "1d": timedelta(days=1),
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
    interval: timedelta = timedelta(minutes=1)

    @property
    def is_complete(self) -> bool:
        return self.status is CoverageStatus.FULL and self.coverage_ratio >= 0.999

    @property
    def missing_candles(self) -> int:
        return sum(int((end - start) / self.interval) for start, end in self.gaps)


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
    """Build exchange candle opens from normalized market-grid boundaries.

    Source seconds are never used as candle-grid coordinates. A 20:38:30
    source timestamp maps to the 20:38:00 candle; a 24h request therefore
    contains exactly 1440 one-minute candle opens through 20:37:00 next day.
    """
    start = _utc(requested_start)
    end = _utc(requested_end)
    grid_start = _floor_to_grid(start, interval)
    grid_end = _floor_to_grid(end, interval)
    if grid_end <= grid_start:
        return grid_start, grid_start - interval, []
    expected_count = int((grid_end - grid_start) / interval)
    expected = [grid_start + interval * i for i in range(expected_count)]
    return grid_start, expected[-1], expected


def calculate_historical_coverage(*, requested_start: datetime, requested_end: datetime, candle_times: Iterable[datetime], interval: timedelta) -> HistoricalCoverage:
    start, end = _utc(requested_start), _utc(requested_end)
    if start >= end:
        raise ValueError("Historical coverage bounds are invalid")
    if interval.total_seconds() <= 0:
        raise ValueError("Historical coverage interval must be positive")

    market_grid_start, market_grid_end, expected_times = _expected_market_grid(
        requested_start=start, requested_end=end, interval=interval
    )
    expected_set = set(expected_times)
    expected = len(expected_times)
    normalized = {_utc(item) for item in candle_times}
    observed = sorted(normalized & expected_set)
    if not observed:
        return HistoricalCoverage(start, end, None, None, expected, 0, 0.0, CoverageStatus.UNAVAILABLE, interval=interval)

    actual_start, actual_end = observed[0], observed[-1]
    actual = len(observed)
    missing = sorted(expected_set - set(observed))
    gaps: list[tuple[datetime, datetime]] = []
    if missing:
        gap_start = previous = missing[0]
        for timestamp in missing[1:]:
            if timestamp != previous + interval:
                gaps.append((gap_start, previous + interval))
                gap_start = timestamp
            previous = timestamp
        gaps.append((gap_start, previous + interval))

    boundaries_complete = actual_start == market_grid_start and actual_end == market_grid_end
    status = CoverageStatus.FULL if boundaries_complete and not missing else CoverageStatus.GAPPED if boundaries_complete else CoverageStatus.PARTIAL_WINDOW
    ratio = min(1.0, actual / expected) if expected else 0.0
    return HistoricalCoverage(start, end, actual_start, actual_end, expected, actual, ratio, status, tuple(gaps), interval)
