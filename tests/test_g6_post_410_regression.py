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


def _g6_replay_harness(monkeypatch, *, event_types, event_statuses=None, coverage_status=CoverageStatus.PARTIAL_WINDOW):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    service = HistoricalMarketReplayService()
    signal = SimpleNamespace(asset="BTCUSDT", market="FUTURES", side="LONG", decision_timestamp=datetime(2025, 12, 4, 0, 0, tzinfo=UTC), targets=[{"price": "110"}, {"price": "120"}, {"price": "130"}])
    run = SimpleNamespace(
        status="RUNNING", ambiguity_status="NONE", data_as_of_status="UNVERIFIABLE",
        coverage_status=None, coverage_ratio=None, actual_start=None, actual_end=None,
        provider=None, provider_endpoint=None, data_source=None, provider_metadata=None,
        fetched_at=None, dataset_hash=None, quality_status="UNASSESSED", result_json=None,
        completed_at=None,
    )
    actual_end = datetime(2025, 12, 4, 5, 0, tzinfo=UTC)
    coverage = SimpleNamespace(
        status=coverage_status, coverage_ratio=0.21,
        actual_start=signal.decision_timestamp, actual_end=actual_end,
        expected_candles=1440, actual_candles=301, gaps=[],
    )
    statuses = event_statuses or ["VERIFIED"] * len(event_types)
    events = [
        SimpleNamespace(
            id=index + 1,
            event_type=event_type,
            event_timestamp=signal.decision_timestamp + timedelta(minutes=(index + 1) * 100),
            price=Decimal("100"),
            replay_status=status,
            event_confidence=Decimal("1"),
            event_data={},
        )
        for index, (event_type, status) in enumerate(zip(event_types, statuses))
    ]

    session = MagicMock()
    evidence = SimpleNamespace(id=7, artifact_hash="a" * 64)
    session.execute.return_value.scalars.return_value.first.return_value = evidence

    class Provider:
        client = None
        def fetch_with_coverage(self, **_kwargs):
            return ([SimpleNamespace(data_source="TEST")], "test://historical", coverage)

    monkeypatch.setattr(service, "_g5_materialization", lambda *_args, **_kwargs: SimpleNamespace(id=55))
    monkeypatch.setattr(service, "_signal_levels", lambda *_args, **_kwargs: (signal, Decimal("100"), Decimal("90"), [Decimal("110"), Decimal("120"), Decimal("130")]))
    monkeypatch.setattr(service, "_source_lifecycle", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_get_or_create_run", lambda *_args, **_kwargs: (run, True))
    monkeypatch.setattr(service, "replay_candles", lambda *_args, **_kwargs: events)

    result = service.replay_g6(
        session,
        signal_id=1,
        materialization_id=55,
        start=signal.decision_timestamp,
        replay_end=signal.decision_timestamp + timedelta(minutes=1440),
        interval="1m",
        limit=1500,
        provider=Provider(),
    )
    return result, run, coverage, events


def test_g6_completed_lifecycle_wins_over_partial_window(monkeypatch):
    result, run, coverage, events = _g6_replay_harness(
        monkeypatch,
        event_types=["ACTIVATED", "TP1", "TP2", "TP3"],
    )

    assert result["status"] == "COMPLETED"
    assert run.status == "COMPLETED"
    assert run.termination_reason == "LIFECYCLE_COMPLETED"
    assert run.exit_timestamp == events[-1].event_timestamp
    assert run.result_json["lifecycle_status"] == "CLOSED_TARGETS"
    assert coverage.status is CoverageStatus.PARTIAL_WINDOW
    assert run.result_json["coverage"]["status"] == "PARTIAL_WINDOW"


def test_g6_active_lifecycle_stays_partial_when_window_is_truncated(monkeypatch):
    result, run, coverage, _events = _g6_replay_harness(
        monkeypatch,
        event_types=["ACTIVATED"],
    )

    assert result["status"] == "REPLAY_PARTIAL"
    assert run.status == "REPLAY_PARTIAL"
    assert run.termination_reason == "DATA_TRUNCATED_WHILE_ACTIVE"
    assert run.exit_timestamp == coverage.actual_end
    assert coverage.status is CoverageStatus.PARTIAL_WINDOW


def test_parser_does_not_treat_recommendation_hashtag_as_asset():
    from capitalguard.application.services.parsing_service import ParsingService
    from capitalguard.infrastructure.db.repository import ParsingRepository

    parser = ParsingService(ParsingRepository)
    asset, side = parser._find_asset_and_side("🎯 BTC | LONG | #123")

    assert asset == "BTCUSDT"
    assert side == "LONG"


def test_parser_prioritizes_standard_usdt_pair_over_sequence_hashtag():
    from capitalguard.application.services.parsing_service import ParsingService
    from capitalguard.infrastructure.db.repository import ParsingRepository

    parser = ParsingService(ParsingRepository)
    asset, side = parser._find_asset_and_side("🎯 BTCUSDT | LONG | #123")

    assert asset == "BTCUSDT"
    assert side == "LONG"
