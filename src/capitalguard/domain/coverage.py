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
    """Return the market-candle boundary at or immediately before value."""
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    normalized = _utc(value)
    return epoch + ((normalized - epoch) // interval) * interval


def _ceil_to_grid(value: datetime, interval: timedelta) -> datetime:
    """Return the market-candle boundary at or immediately after value."""
    normalized = _utc(value)
    floor = _floor_to_grid(normalized, interval)
    return floor if floor == normalized else floor + interval


def _expected_market_grid(
    *,
    requested_start: datetime,
    requested_end: datetime,
    interval: timedelta,
) -> tuple[datetime, datetime, list[datetime]]:
    """Build expected candle opens on the real market grid.

    The source/replay timestamps may contain arbitrary seconds (for example
    20:38:30), while exchange candles open on interval boundaries. The raw
    request bounds are retained by HistoricalCoverage; only the expected
    candle grid is normalized.

    A candle is expected when its open time is the first grid boundary at or
    after the requested start and at or before the requested end. The floor
    and ceil bounds are retained here to make the normalization explicit and
    to avoid anchoring the expected grid to an arbitrary source timestamp.
    """
    start = _utc(requested_start)
    end = _utc(requested_end)
    market_grid_start = _floor_to_grid(start, interval)
    market_grid_end = _ceil_to_grid(end, interval)

    first_expected = market_grid_start if market_grid_start == start else market_grid_start + interval
    last_expected = market_grid_end if market_grid_end == end else market_grid_end - interval
    if first_expected > last_expected:
        return market_grid_start, market_grid_end, []

    expected_count = int((last_expected - first_expected) // interval) + 1
    expected = [first_expected + interval * index for index in range(expected_count)]
    return market_grid_start, market_grid_end, expected


def calculate_historical_coverage(
    *,
    requested_start: datetime,
    requested_end: datetime,
    candle_times: Iterable[datetime],
    interval: timedelta,
) -> HistoricalCoverage:
    """Classify official candle coverage without fabricating missing data.

    Coverage is evaluated on the exchange candle-open grid rather than on the
    source message's arbitrary second offset. This prevents a valid Binance
    window such as 20:39:00..20:38:00 from being compared against an invalid
    20:38:30..20:38:30 grid.
    """
    start = _utc(requested_start)
    end = _utc(requested_end)
    if start >= end:
        raise ValueError("Historical coverage bounds are invalid")
    if interval.total_seconds() <= 0:
        raise ValueError("Historical coverage interval must be positive")

    _market_grid_start, _market_grid_end, expected_times = _expected_market_grid(
        requested_start=start,
        requested_end=end,
        interval=interval,
    )
    expected_set = set(expected_times)
    expected = len(expected_times)

    normalized_times = {
        _utc(item)
        for item in candle_times
    }
    times = sorted(normalized_times & expected_set)
    actual = len(times)

    if not times:
        return HistoricalCoverage(start, end, None, None, expected, 0, 0.0, CoverageStatus.UNAVAILABLE)

    actual_start, actual_end = times[0], times[-1]
    missing = sorted(expected_set - set(times))

    gaps: list[tuple[datetime, datetime]] = []
    if missing:
        gap_start = previous = missing[0]
        for timestamp in missing[1:]:
            if timestamp != previous + interval:
                gaps.append((gap_start, previous + interval))
                gap_start = timestamp
            previous = timestamp
        gaps.append((gap_start, previous + interval))

    boundaries_complete = bool(expected_times) and actual_start == expected_times[0] and actual_end == expected_times[-1]
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
