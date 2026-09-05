from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from capitalguard.application.services.historical_market_replay_service import (
    HistoricalMarketReplayService,
    MarketCandle,
    REPLAY_VERSION,
)
from capitalguard.application.services.historical_signal_materialization_service import HistoricalSignalMaterializationService
from capitalguard.infrastructure.db.models import HistoricalMarketEvidence, HistoricalReplayRun, HistoricalSignalEvent
from tests.test_historical_signal_materialization_service import accepted_g5_draft


class FakeProvider:
    def __init__(self, candles):
        self.candles = candles
        self.calls = 0

    def fetch(self, **_kwargs):
        self.calls += 1
        return self.candles, "https://provider.test/klines"


def _candles(signal, count=61):
    start = signal.decision_timestamp
    return [
        MarketCandle(
            asset="BTCUSDT",
            market="Futures",
            open_time=start + timedelta(minutes=index),
            open=Decimal("69000"),
            high=Decimal("70000"),
            low=Decimal("68500"),
            close=Decimal("69500"),
            volume=Decimal("1"),
            data_source="BINANCE_FUTURES",
        )
        for index in range(count)
    ]


def test_g6_materialized_signal_replay_links_run_evidence_and_isolation(db_session):
    draft, _revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    from capitalguard.infrastructure.db.models import HistoricalSignalMaterialization
    bridge = db_session.execute(
        select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.signal_id == signal.id)
    ).scalar_one()
    original_eligibility = signal.eligible_for_ranking
    provider = FakeProvider(_candles(signal))
    replay = HistoricalMarketReplayService()
    result = replay.replay_g6(
        db_session,
        signal_id=signal.id,
        materialization_id=bridge.id,
        start=signal.decision_timestamp,
        replay_end=signal.decision_timestamp + timedelta(hours=1),
        provider=provider,
    )

    assert result["status"] == "COMPLETED_UNVERIFIABLE"
    assert result["coverage"].status.value == "FULL"
    run = result["run"]
    assert run.replay_version == REPLAY_VERSION
    assert run.status == "COMPLETED_UNVERIFIABLE"
    assert run.coverage_status == "FULL"
    assert run.coverage_ratio == 1.0
    assert run.data_as_of_status == "UNVERIFIABLE"
    assert provider.calls == 1
    events = db_session.execute(select(HistoricalSignalEvent).where(HistoricalSignalEvent.replay_run_id == run.id)).scalars().all()
    evidence = db_session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.replay_run_id == run.id)).scalar_one()
    assert events
    assert evidence.artifact_hash
    assert run.dataset_hash == evidence.artifact_hash
    assert all(event.replay_run_id == run.id for event in events)
    assert signal.eligible_for_ranking == original_eligibility

    second = replay.replay_g6(
        db_session,
        signal_id=signal.id,
        materialization_id=bridge.id,
        start=signal.decision_timestamp,
        replay_end=signal.decision_timestamp + timedelta(hours=1),
        provider=provider,
    )
    assert second["run"].id == run.id
    assert provider.calls == 1


def test_g6_partial_window_is_persisted_without_fabricating_a_complete_result(db_session):
    draft, _revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    from capitalguard.infrastructure.db.models import HistoricalSignalMaterialization
    bridge = db_session.execute(
        select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.signal_id == signal.id)
    ).scalar_one()

    result = HistoricalMarketReplayService().replay_g6(
        db_session,
        signal_id=signal.id,
        materialization_id=bridge.id,
        start=signal.decision_timestamp,
        replay_end=signal.decision_timestamp + timedelta(hours=1),
        provider=FakeProvider(_candles(signal, count=1)),
    )

    # With no terminal lifecycle event, truncated active replay remains partial.
    # If a target/stop had been reached, lifecycle-first semantics would allow
    # COMPLETED_UNVERIFIABLE while preserving PARTIAL_WINDOW coverage.
    assert result["status"] == "REPLAY_PARTIAL"
    assert result["coverage"].status.value == "PARTIAL_WINDOW"
    assert result["run"].coverage_status == "PARTIAL_WINDOW"
    assert result["run"].coverage_ratio < 1.0
    assert result["run"].actual_end == signal.decision_timestamp


def test_g6_off_grid_source_timestamp_uses_market_grid_and_completes(db_session):
    draft, _revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    from capitalguard.infrastructure.db.models import HistoricalSignalMaterialization
    bridge = db_session.execute(
        select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.signal_id == signal.id)
    ).scalar_one()

    source_time = datetime(2025, 12, 4, 20, 38, 30, tzinfo=timezone.utc)
    signal.decision_timestamp = source_time
    market_times = [
        datetime(2025, 12, 4, 20, 39, tzinfo=timezone.utc) + timedelta(minutes=index)
        for index in range(1440)
    ]
    candles = [
        MarketCandle(
            asset="BTCUSDT",
            market="Futures",
            open_time=open_time,
            open=Decimal("93000"),
            high=Decimal("93500"),
            low=Decimal("92500"),
            close=Decimal("93000"),
            volume=Decimal("1"),
            data_source="BINANCE_FUTURES",
        )
        for open_time in market_times
    ]

    result = HistoricalMarketReplayService().replay_g6(
        db_session,
        signal_id=signal.id,
        materialization_id=bridge.id,
        start=source_time,
        replay_end=source_time + timedelta(hours=24),
        provider=FakeProvider(candles),
    )

    assert result["coverage"].status.value == "FULL"
    assert result["coverage"].expected_candles == 1440
    assert result["coverage"].actual_candles == 1440
    assert result["coverage"].coverage_ratio == 1.0
    assert result["status"] in {"COMPLETED", "COMPLETED_UNVERIFIABLE"}
    assert result["run"].coverage_status == "FULL"
    assert result["run"].status in {"COMPLETED", "COMPLETED_UNVERIFIABLE"}


def test_g6_marks_ohlcv_tp_sl_conflict_ambiguous(db_session):
    draft, _revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    from capitalguard.infrastructure.db.models import HistoricalSignalMaterialization
    bridge = db_session.execute(
        select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.signal_id == signal.id)
    ).scalar_one()
    signal.stop_loss = Decimal("68000")
    signal.targets = [{"price": "70000", "close_percent": 100}]
    candles = _candles(signal)
    candles[0] = MarketCandle(
        asset=candles[0].asset,
        market=candles[0].market,
        open_time=candles[0].open_time,
        open=candles[0].open,
        high=Decimal("70000"),
        low=Decimal("67500"),
        close=candles[0].close,
        volume=candles[0].volume,
        data_source=candles[0].data_source,
    )
    provider = FakeProvider(candles)
    result = HistoricalMarketReplayService().replay_g6(
        db_session,
        signal_id=signal.id,
        materialization_id=bridge.id,
        start=signal.decision_timestamp,
        replay_end=signal.decision_timestamp + timedelta(hours=1),
        provider=provider,
    )
    assert result["status"] == "COMPLETED_UNVERIFIABLE"
    assert result["coverage"].status.value == "FULL"
    assert result["run"].ambiguity_status == "AMBIGUOUS"
    assert any(event.replay_status == "AMBIGUOUS" for event in result["events"])
    assert not any(event.event_type in {"SL", "TP1"} for event in result["events"])


def test_g6_replay_gate_isolated_from_reputation():
    from capitalguard.application.services.historical_replay_gate_service import HistoricalReplayGateService

    report = HistoricalReplayGateService().assess_replay(parse_status="PARSED", market_data_available=True)

    assert report.replay_allowed is True
    assert report.reputation_eligible is False
    assert report.status == "REPLAY_READY"


def test_g6_replay_requires_g5_materialization(db_session):
    draft, _revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    replay = HistoricalMarketReplayService()
    missing_materialization_id = 999999

    try:
        replay.replay_g6(
            db_session,
            signal_id=signal.id,
            materialization_id=missing_materialization_id,
            start=signal.decision_timestamp,
            replay_end=signal.decision_timestamp + timedelta(hours=1),
            provider=FakeProvider(_candles(signal)),
        )
    except ValueError as exc:
        assert "G5 materialization" in str(exc)
    else:
        raise AssertionError("G6 must reject a signal without its G5 materialization")
