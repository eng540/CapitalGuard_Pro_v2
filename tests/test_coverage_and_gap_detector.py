from datetime import datetime, timedelta, timezone

import pytest

from capitalguard.domain.coverage import CoverageStatus, calculate_historical_coverage, interval_delta

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(minutes=4)


def test_full_coverage_has_complete_ratio_and_no_gaps():
    times = [START + timedelta(minutes=i) for i in range(5)]
    coverage = calculate_historical_coverage(requested_start=START, requested_end=END, candle_times=times, interval=timedelta(minutes=1))
    assert coverage.status is CoverageStatus.FULL
    assert coverage.coverage_ratio == 1.0
    assert coverage.is_complete is True
    assert coverage.gaps == ()


def test_partial_window_when_provider_does_not_reach_requested_end():
    times = [START + timedelta(minutes=i) for i in range(3)]
    coverage = calculate_historical_coverage(requested_start=START, requested_end=END, candle_times=times, interval=timedelta(minutes=1))
    assert coverage.status is CoverageStatus.PARTIAL_WINDOW
    assert coverage.actual_start == START
    assert coverage.actual_end == START + timedelta(minutes=2)
    assert coverage.coverage_ratio == pytest.approx(3 / 4)


@pytest.mark.parametrize("second", [15, 30, 45])
def test_off_grid_source_seconds_do_not_create_false_partial_window(second):
    requested_start = datetime(2025, 12, 4, 20, 38, second, tzinfo=timezone.utc)
    requested_end = requested_start + timedelta(hours=24)
    expected_times = [datetime(2025, 12, 4, 20, 39, tzinfo=timezone.utc) + timedelta(minutes=index) for index in range(1440)]
    coverage = calculate_historical_coverage(requested_start=requested_start, requested_end=requested_end, candle_times=expected_times, interval=timedelta(minutes=1))
    assert coverage.status is CoverageStatus.FULL
    assert coverage.expected_candles == 1440
    assert coverage.actual_candles == 1440
    assert coverage.coverage_ratio == 1.0
    assert coverage.actual_start == expected_times[0]
    assert coverage.actual_end == expected_times[-1]


def test_exact_btcusdt_reproduction_is_full_1440_of_1440():
    requested_start = datetime(2025, 12, 4, 20, 38, 30, tzinfo=timezone.utc)
    requested_end = datetime(2025, 12, 5, 20, 38, 30, tzinfo=timezone.utc)
    times = [datetime(2025, 12, 4, 20, 39, tzinfo=timezone.utc) + timedelta(minutes=index) for index in range(1440)]
    coverage = calculate_historical_coverage(requested_start=request_start, requested_end=requested_end, candle_times=times, interval=timedelta(minutes=1))
    assert coverage.status is CoverageStatus.FULL
    assert coverage.expected_candles == 1440
    assert coverage.actual_candles == 1440
    assert coverage.coverage_ratio == 1.0


def test_gapped_window_when_boundaries_are_present_but_internal_candle_is_missing():
    times = [START, START + timedelta(minutes=1), START + timedelta(minutes=3), END]
    coverage = calculate_historical_coverage(requested_start=START, requested_end=END, candle_times=times, interval=timedelta(minutes=1))
    assert coverage.status is CoverageStatus.GAPPED
    assert coverage.gaps == ((START + timedelta(minutes=2), START + timedelta(minutes=3)),)


def test_partial_window_when_off_grid_beginning_is_missing():
    requested_start = datetime(2025, 12, 4, 20, 38, 30, tzinfo=timezone.utc)
    requested_end = datetime(2025, 12, 4, 21, 38, 30, tzinfo=timezone.utc)
    times = [datetime(2025, 12, 4, 20, 40, tzinfo=timezone.utc) + timedelta(minutes=index) for index in range(59)]
    coverage = calculate_historical_coverage(requested_start=request_start, requested_end=requested_end, candle_times=times, interval=timedelta(minutes=1))
    assert coverage.status is CoverageStatus.PARTIAL_WINDOW
    assert coverage.expected_candles == 60
    assert coverage.actual_candles == 59


def test_unavailable_window_has_zero_observations():
    coverage = calculate_historical_coverage(requested_start=START, requested_end=END, candle_times=[], interval=timedelta(minutes=1))
    assert coverage.status is CoverageStatus.UNAVAILABLE
    assert coverage.actual_candles == 0
    assert coverage.coverage_ratio == 0.0


def test_interval_contract_is_centralized():
    assert interval_delta("1m") == timedelta(minutes=1)
    assert interval_delta("1h") == timedelta(hours=1)
