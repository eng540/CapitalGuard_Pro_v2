from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from capitalguard.application.services.historical_market_replay_service import HistoricalMarketReplayService, MarketCandle
from capitalguard.application.services.historical_signal_materialization_service import HistoricalSignalMaterializationService
from capitalguard.infrastructure.db.models import HistoricalSignalMaterialization
from capitalguard.domain.coverage import calculate_historical_coverage, interval_delta
from tests.test_historical_signal_materialization_service import accepted_g5_draft

UTC = timezone.utc

class AdaptiveFakeProvider:
    def __init__(self, daily, minutes):
        self.daily = daily; self.minutes = minutes; self.daily_calls = []; self.minute_calls = []; self.client = None
    def fetch_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        rows = [c for c in self.daily if kwargs["start"] <= c.open_time < kwargs["end"]]
        return rows, "daily-test", calculate_historical_coverage(requested_start=kwargs["start"], requested_end=kwargs["end"], candle_times=(c.open_time for c in rows), interval=interval_delta("1d"))
    def fetch_minute_day(self, **kwargs):
        self.minute_calls.append(kwargs)
        rows = [c for c in self.minutes if kwargs["start"] <= c.open_time < kwargs["end"]]
        return rows, "minute-test", calculate_historical_coverage(requested_start=kwargs["start"], requested_end=kwargs["end"], candle_times=(c.open_time for c in rows), interval=interval_delta("1m"))

def _setup(db_session):
    draft, _ = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    bridge = db_session.execute(select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.signal_id == signal.id)).scalar_one()
    return signal, bridge

def c(t, high=101, low=99):
    return MarketCandle(asset="BTCUSDT", market="Futures", open_time=t, open=Decimal("100"), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal("100"), volume=Decimal("1"), data_source="BINANCE_FUTURES")

def test_annual_macro_replays_only_critical_days_for_150_days(db_session):
    signal, bridge = _setup(db_session)
    signal.decision_timestamp = datetime(2025, 1, 1, 12, 30, tzinfo=UTC); signal.entry = Decimal("100"); signal.stop_loss = Decimal("90"); signal.targets = [{"price": "110", "close_percent": 100}]
    provider = AdaptiveFakeProvider([c(datetime(2025,1,1,tzinfo=UTC)), c(datetime(2025,3,2,tzinfo=UTC), high=111)], [c(datetime(2025,1,1,12,30,tzinfo=UTC)), c(datetime(2025,3,2,10,tzinfo=UTC), high=111)])
    result = HistoricalMarketReplayService().replay_g6(db_session, signal_id=signal.id, materialization_id=bridge.id, start=signal.decision_timestamp, replay_end=datetime(2025,5,31,tzinfo=UTC), provider=provider)
    assert result["status"] == "COMPLETED"
    assert len(provider.daily_calls) == 1 and len(provider.minute_calls) == 2
    assert result["run"].provider_metadata["critical_days_count"] == 2
    assert result["run"].provider_metadata["resolution_mode"] == "HYBRID_MACRO_DRILLDOWN"

def test_day_zero_rapid_exit_starts_at_signal_time_and_stays_inside_day(db_session):
    signal, bridge = _setup(db_session)
    signal.decision_timestamp = datetime(2025,1,1,22,30,tzinfo=UTC); signal.entry = Decimal("100"); signal.stop_loss = Decimal("90"); signal.targets = [{"price":"110","close_percent":100}]
    provider = AdaptiveFakeProvider([c(datetime(2025,1,1,tzinfo=UTC), high=111)], [c(datetime(2025,1,1,22,30,tzinfo=UTC), high=111), c(datetime(2025,1,2,0,0,tzinfo=UTC), high=999)])
    result = HistoricalMarketReplayService().replay_g6(db_session, signal_id=signal.id, materialization_id=bridge.id, start=signal.decision_timestamp, replay_end=datetime(2025,1,2,12,tzinfo=UTC), provider=provider)
    assert result["status"] == "COMPLETED"
    assert provider.minute_calls[0]["start"] == signal.decision_timestamp
    assert provider.minute_calls[0]["end"] <= datetime(2025,1,2,tzinfo=UTC)
    assert all(c.open_time < datetime(2025,1,2,tzinfo=UTC) for c in provider.minutes if c.open_time >= provider.minute_calls[0]["start"] and c.open_time < provider.minute_calls[0]["end"])

def test_multi_year_cursor_preserves_activation_and_remaining_targets(db_session):
    signal, bridge = _setup(db_session)
    signal.decision_timestamp = datetime(2025,1,1,10,tzinfo=UTC); signal.entry = Decimal("100"); signal.stop_loss = Decimal("90"); signal.targets = [{"price":"110","close_percent":50},{"price":"120","close_percent":50}]
    provider = AdaptiveFakeProvider([c(datetime(2025,1,1,tzinfo=UTC)), c(datetime(2026,2,1,tzinfo=UTC), high=111), c(datetime(2026,1,15,tzinfo=UTC), high=121)], [c(datetime(2025,1,1,10,tzinfo=UTC)), c(datetime(2026,2,1,9,tzinfo=UTC), high=111), c(datetime(2026,1,15,9,tzinfo=UTC), high=121)])
    result = HistoricalMarketReplayService().replay_g6(db_session, signal_id=signal.id, materialization_id=bridge.id, start=signal.decision_timestamp, replay_end=datetime(2026,3,1,tzinfo=UTC), provider=provider)
    assert result["status"] == "COMPLETED"
    assert len(provider.daily_calls) == 2
    assert len(provider.minute_calls) == 3
    event_types = [e.event_type for e in result["events"]]
    assert event_types.count("ACTIVATED") == 1 and "TP1" in event_types and "TP2" in event_types
    assert result["run"].provider_metadata["remaining_target_indices"] == []
