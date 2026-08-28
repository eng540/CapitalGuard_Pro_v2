from datetime import datetime, timedelta, timezone

import pytest

from capitalguard.domain.coverage import CoverageStatus, calculate_historical_coverage, interval_delta


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(minutes=4)


def test_full_coverage_has_complete_ratio_and_no_gaps():
    times = [START + timedelta(minutes=i) for i in range(5)]
    coverage = calculate_historical_coverage(
        requested_start=START,
        requested_end=END,
        candle_times=times,
        interval=timedelta(minutes=1),
    )

    assert coverage.status is CoverageStatus.FULL
    assert coverage.coverage_ratio == 1.0
    assert coverage.is_complete is True
    assert coverage.gaps == ()


def test_partial_window_when_provider_does_not_reach_requested_end():
    times = [START + timedelta(minutes=i) for i in range(3)]
    coverage = calculate_historical_coverage(
        requested_start=START,
        requested_end=END,
        candle_times=times,
        interval=timedelta(minutes=1),
    )

    assert coverage.status is CoverageStatus.PARTIAL_WINDOW
    assert coverage.actual_start == START
    assert coverage.actual_end == START + timedelta(minutes=2)
    assert coverage.coverage_ratio == pytest.approx(3 / 5)


def test_gapped_window_when_boundaries_are_present_but_internal_candle_is_missing():
    times = [START, START + timedelta(minutes=1), START + timedelta(minutes=3), END]
    coverage = calculate_historical_coverage(
        requested_start=START,
        requested_end=END,
        candle_times=times,
        interval=timedelta(minutes=1),
    )

    assert coverage.status is CoverageStatus.GAPPED
    assert coverage.gaps == ((START + timedelta(minutes=2), START + timedelta(minutes=3)),)


def test_unavailable_window_has_zero_observations():
    coverage = calculate_historical_coverage(
        requested_start=START,
        requested_end=END,
        candle_times=[],
        interval=timedelta(minutes=1),
    )

    assert coverage.status is CoverageStatus.UNAVAILABLE
    assert coverage.actual_candles == 0
    assert coverage.coverage_ratio == 0.0


def test_interval_contract_is_centralized():
    assert interval_delta("1m") == timedelta(minutes=1)
    assert interval_delta("1h") == timedelta(hours=1)
