from datetime import datetime, timedelta, timezone

from capitalguard.domain.coverage import CoverageStatus, calculate_historical_coverage


def test_second_offset_source_timestamp_uses_exchange_grid():
    start = datetime(2025, 12, 4, 20, 38, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=1440)
    candles = [start.replace(second=0, microsecond=0) + timedelta(minutes=i) for i in range(1440)]

    coverage = calculate_historical_coverage(
        requested_start=start,
        requested_end=end,
        candle_times=candles,
        interval=timedelta(minutes=1),
    )

    assert coverage.status is CoverageStatus.FULL
    assert coverage.expected_candles == 1441
    assert coverage.actual_candles == 1440
    assert coverage.actual_start == start.replace(second=0, microsecond=0)


def test_real_gap_remains_gapped():
    start = datetime(2025, 12, 4, 20, 38, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=10)
    grid_start = start.replace(second=0, microsecond=0)
    candles = [grid_start + timedelta(minutes=i) for i in range(11) if i != 5]

    coverage = calculate_historical_coverage(
        requested_start=start,
        requested_end=end,
        candle_times=candles,
        interval=timedelta(minutes=1),
    )

    assert coverage.status is CoverageStatus.GAPPED
    assert coverage.missing_candles == 1
