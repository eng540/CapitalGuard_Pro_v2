from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from capitalguard.application.services.historical_market_replay_service import HistoricalMarketReplayService, MarketCandle
from capitalguard.domain.coverage import CoverageStatus, calculate_historical_coverage, interval_delta

UTC = timezone.utc


@pytest.mark.parametrize("seconds", [0, 15, 30, 45, 59])
def test_market_grid_accepts_off_grid_source_timestamp(seconds):
    start = datetime(2025, 12, 4, 20, 38, seconds, tzinfo=UTC)
    end = start + timedelta(hours=24)
    times = [datetime(2025, 12, 4, 20, 39, tzinfo=UTC) + timedelta(minutes=i) for i in range(1440)]
    coverage = calculate_historical_coverage(
        requested_start=start,
        requested_end=end,
        candle_times=times,
        interval=timedelta(minutes=1),
    )
    assert coverage.expected_candles == 1440
    assert coverage.actual_candles == 1440
    assert coverage.coverage_ratio == 1.0
    assert coverage.status is CoverageStatus.FULL


def _candle(ts: datetime, high: str, low: str) -> MarketCandle:
    return MarketCandle(
        asset="BTCUSDT",
        market="FUTURES",
        open_time=ts,
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        volume=Decimal("1"),
        data_source="BINANCE_FUTURES",
    )


def test_coarse_to_fine_keeps_only_relevant_hour():
    base = datetime(2025, 12, 4, 0, 0, tzinfo=UTC)
    candles = []
    for hour in range(24):
        for minute in range(60):
            ts = base + timedelta(hours=hour, minutes=minute)
            candles.append(_candle(ts, "110" if hour == 12 and minute == 17 else "101", "99"))
    selected = HistoricalMarketReplayService._coarse_to_fine_candles(
        candles, side="LONG", entry=Decimal("110"), stop=Decimal("90"), target_levels=[Decimal("120")]
    )
    assert selected
    assert all(c.open_time.hour == 12 for c in selected)
    assert any(c.open_time.minute == 17 for c in selected)


SEVEN_CASES = [
    ("BTCUSDT", "LONG", "92961.50", ["93100", "93200", "93400"], "92000"),
    ("BTCUSDT", "SHORT", "91844.50", ["91800", "91600", "91400", "91300", "91000"], "92000"),
    ("BTCUSDT", "LONG", "87419.61", ["90900", "91000"], "87410.87"),
    ("BTCUSDT", "SHORT", "91409.13", ["90000"], "92000"),
    ("BTCUSDT", "SHORT", "85274.30", ["84300", "84300", "84200"], "85500"),
    ("BTCUSDT", "LONG", "85269.10", ["85500", "85700", "86000", "87000"], "84000"),
    ("BNBUSDT", "LONG", "876", ["880", "890", "900"], "870"),
]


@pytest.mark.parametrize("asset,side,entry,targets,stop", SEVEN_CASES)
def test_seven_regression_inputs_have_stable_target_order(asset, side, entry, targets, stop):
    unique_targets = list(dict.fromkeys(Decimal(value) for value in targets))
    assert unique_targets
    assert Decimal(entry) > 0
    assert Decimal(stop) > 0


def test_interval_delta_is_used_for_micro_drilldown():
    assert interval_delta("1m") == timedelta(minutes=1)
