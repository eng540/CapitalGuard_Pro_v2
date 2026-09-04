from datetime import datetime, timedelta, timezone

from capitalguard.domain.coverage import CoverageStatus, calculate_historical_coverage

UTC = timezone.utc


def test_btcusdt_2025_12_03_source_timestamp_has_full_24h_market_grid():
    source_timestamp = datetime(2025, 12, 3, 17, 55, 42, tzinfo=UTC)
    replay_end = source_timestamp + timedelta(hours=24)
    first_market_candle = source_timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
    candle_times = [first_market_candle + timedelta(minutes=i) for i in range(1440)]
    coverage = calculate_historical_coverage(
        requested_start=source_timestamp,
        requested_end=replay_end,
        candle_times=candle_times,
        interval=timedelta(minutes=1),
    )
    assert coverage.status is CoverageStatus.FULL
    assert coverage.coverage_ratio == 1.0
    assert coverage.expected_candles == 1440
    assert coverage.actual_candles == 1440
    assert coverage.actual_start == first_market_candle
    assert coverage.actual_end == first_market_candle + timedelta(minutes=1439)
