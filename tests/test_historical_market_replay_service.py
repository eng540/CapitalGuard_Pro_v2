from datetime import datetime, timezone
from decimal import Decimal

import pytest

from capitalguard.application.services.historical_market_replay_service import (
    HistoricalMarketReplayService,
    MarketObservation,
)
from capitalguard.application.services.historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError


def _time(day: int, hour: int):
    return datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc)


def test_replay_records_activation_targets_and_eligibility(db_session):
    signal_service = HistoricalSignalService()
    evidence = signal_service.ingest_evidence(
        db_session,
        source_kind="AUTHORIZED_USER_HISTORY",
        message_timestamp=_time(1, 9),
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110@50% TP2 120@50%",
    )
    signal = signal_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_time(1, 9),
        analyst_id=1,
        asset="BTCUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("90"),
        targets=[{"price": "110", "close_percent": 50}, {"price": "120", "close_percent": 50}],
    )
    events = HistoricalMarketReplayService().replay(
        db_session,
        signal_id=signal.id,
        replay_end=_time(1, 15),
        observations=[
            MarketObservation("BTCUSDT", "Futures", _time(1, 10), Decimal("100"), "historical-test"),
            MarketObservation("BTCUSDT", "Futures", _time(1, 11), Decimal("110"), "historical-test"),
            MarketObservation("BTCUSDT", "Futures", _time(1, 12), Decimal("120"), "historical-test"),
        ],
    )

    assert [event.event_type for event in events] == ["ACTIVATED", "TP1", "TP2"]
    assert all(event.replay_status == "VERIFIED" for event in events)
    assert signal.eligible_for_ranking is True


def test_replay_rejects_observation_after_replay_end(db_session):
    signal_service = HistoricalSignalService()
    evidence = signal_service.ingest_evidence(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        message_timestamp=_time(1, 9),
        raw_text="#ETHUSDT LONG Entry 100",
    )
    signal = signal_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_time(1, 9),
        asset="ETHUSDT",
        side="LONG",
        entry=Decimal("100"),
    )

    with pytest.raises(HistoricalSignalValidationError, match="replay_end"):
        HistoricalMarketReplayService().replay(
            db_session,
            signal_id=signal.id,
            replay_end=_time(1, 10),
            observations=[MarketObservation("ETHUSDT", "Futures", _time(1, 11), Decimal("101"), "test")],
        )
