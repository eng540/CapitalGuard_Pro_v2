from datetime import datetime, timedelta, timezone
from decimal import Decimal

from capitalguard.application.services.historical_market_replay_service import HistoricalMarketReplayService, MarketCandle
from capitalguard.domain.coverage import calculate_historical_coverage, interval_delta
from tests.test_g6_annual_macro_replay import _setup

UTC = timezone.utc


def candle(t, high=101, low=99):
    return MarketCandle(asset="BTCUSDT", market="Futures", open_time=t, open=Decimal("100"), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal("100"), volume=Decimal("1"), data_source="FAKE")


class Provider:
    def __init__(self, daily, minutes): self.daily, self.minutes = daily, minutes
    def fetch_daily(self, **kwargs):
        rows = [item for item in self.daily if kwargs["start"] <= item.open_time < kwargs["end"]]
        return rows, "daily-test", calculate_historical_coverage(requested_start=kwargs["start"], requested_end=kwargs["end"], candle_times=(item.open_time for item in rows), interval=interval_delta("1d"))
    def fetch_minute_day(self, **kwargs):
        day = kwargs["start"].replace(hour=0, minute=0, second=0, microsecond=0); rows = list(self.minutes.get(day, []))
        return rows, "minute-test", calculate_historical_coverage(requested_start=kwargs["start"], requested_end=kwargs["end"], candle_times=(item.open_time for item in rows), interval=interval_delta("1m"))


def minute_series(start, end, high, low):
    rows=[]; cursor=start.replace(second=0,microsecond=0)
    while cursor < end:
        rows.append(candle(cursor, high, low)); cursor += timedelta(minutes=1)
    return rows


def test_98k_pending_order_has_zero_pnl(db_session):
    signal, bridge = _setup(db_session); signal.decision_timestamp=datetime(2025,11,23,18,54,40,tzinfo=UTC); signal.entry=Decimal("98000"); signal.stop_loss=Decimal("97000"); signal.targets=[{"price":"99000","close_percent":50},{"price":"100000","close_percent":50}]
    days=[candle(datetime(2025,11,23,tzinfo=UTC),90000,88000),candle(datetime(2025,11,24,tzinfo=UTC),89228,87000)]
    day0_start=datetime(2025,11,23,tzinfo=UTC); day0_end=datetime(2025,11,24,tzinfo=UTC)
    minutes={day0_start: minute_series(datetime(2025,11,23,18,54,tzinfo=UTC), day0_end, 90000, 88000)}
    result=HistoricalMarketReplayService().replay_g6(db_session,signal_id=signal.id,materialization_id=bridge.id,start=signal.decision_timestamp,replay_end=datetime(2025,11,25,tzinfo=UTC),provider=Provider(days,minutes))
    assert result["status"]=="COMPLETED"; assert result["run"].result_json["lifecycle_status"]=="PENDING_ORDER"; assert result["run"].result_json["pnl_percentage"]=="0"; assert result["events"]==[]


def test_daily_entry_plus_stop_is_unverifiable(db_session):
    signal, bridge = _setup(db_session); signal.decision_timestamp=datetime(2025,1,1,12,tzinfo=UTC); signal.entry=Decimal("100"); signal.stop_loss=Decimal("90"); signal.targets=[{"price":"110","close_percent":100}]
    days=[candle(datetime(2025,1,1,tzinfo=UTC)),candle(datetime(2025,1,2,tzinfo=UTC),110,90)]
    result=HistoricalMarketReplayService().replay_g6(db_session,signal_id=signal.id,materialization_id=bridge.id,start=signal.decision_timestamp,replay_end=datetime(2025,1,3,tzinfo=UTC),provider=Provider(days,{}))
    assert result["status"]=="COMPLETED_UNVERIFIABLE"; assert result["run"].result_json["lifecycle_status"]=="CLOSED_UNVERIFIABLE"


def test_partial_after_activation_stays_partial(db_session):
    signal, bridge = _setup(db_session); signal.decision_timestamp=datetime(2025,1,1,12,tzinfo=UTC); signal.entry=Decimal("100"); signal.stop_loss=Decimal("90"); signal.targets=[{"price":"110","close_percent":100}]
    days=[candle(datetime(2025,1,1,tzinfo=UTC)),candle(datetime(2025,1,2,tzinfo=UTC)),candle(datetime(2025,1,3,tzinfo=UTC),111,99)]; minutes={datetime(2025,1,1,tzinfo=UTC):[candle(datetime(2025,1,1,12,tzinfo=UTC),101,99)]}
    result=HistoricalMarketReplayService().replay_g6(db_session,signal_id=signal.id,materialization_id=bridge.id,start=signal.decision_timestamp,replay_end=datetime(2025,1,4,tzinfo=UTC),provider=Provider(days,minutes))
    assert result["status"]=="REPLAY_PARTIAL"; assert result["run"].result_json["lifecycle_status"]=="CLOSED_TARGETS"; assert result["run"].termination_reason=="DATA_TRUNCATED_WHILE_ACTIVE"
