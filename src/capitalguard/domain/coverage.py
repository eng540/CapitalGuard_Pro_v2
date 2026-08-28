from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable


class CoverageStatus(str, Enum):
    FULL = "FULL"
    PARTIAL_WINDOW = "PARTIAL_WINDOW"
    GAPPED = "GAPPED"
    UNAVAILABLE = "UNAVAILABLE"


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


def calculate_historical_coverage(
    *,
    requested_start: datetime,
    requested_end: datetime,
    candle_times: Iterable[datetime],
    interval: timedelta,
) -> HistoricalCoverage:
    """Classify a candle window without fabricating missing market data."""
    if requested_start.tzinfo is None or requested_end.tzinfo is None:
        raise ValueError("Historical coverage bounds must be timezone-aware")
    if requested_start >= requested_end:
        raise ValueError("Historical coverage bounds are invalid")
    if interval.total_seconds() <= 0:
        raise ValueError("Historical coverage interval must be positive")

    start = requested_start
    end = requested_end
    times = sorted({item for item in candle_times})
    expected = int((end - start) // interval) + 1
    actual = len(times)

    if not times:
        return HistoricalCoverage(start, end, None, None, expected, 0, 0.0, CoverageStatus.UNAVAILABLE)

    actual_start, actual_end = times[0], times[-1]
    expected_set = {start + interval * index for index in range(expected)}
    actual_set = set(times)
    missing = sorted(expected_set - actual_set)

    gaps: list[tuple[datetime, datetime]] = []
    if missing:
        gap_start = previous = missing[0]
        for timestamp in missing[1:]:
            if timestamp != previous + interval:
                gaps.append((gap_start, previous + interval))
                gap_start = timestamp
            previous = timestamp
        gaps.append((gap_start, previous + interval))

    boundaries_complete = actual_start == start and actual_end >= end
    if boundaries_complete and gaps:
        status = CoverageStatus.GAPPED
    elif boundaries_complete and actual == expected and not missing:
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
