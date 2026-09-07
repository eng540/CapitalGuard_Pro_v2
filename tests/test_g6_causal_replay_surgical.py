from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from capitalguard.application.services.adaptive_historical_replay import AdaptiveHistoricalReplayPlanner
from capitalguard.infrastructure.market.historical_ohlcv_provider import BinanceHistoricalOhlcvProvider


UTC = timezone.utc


def _record(ts: datetime):
    return SimpleNamespace(
        symbol="BTCUSDT",
        market="SPOT",
        open_time=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        provider_endpoint="test://binance/klines",
    )


class _Client:
    def __init__(self):
        self.calls = []

    def get_historical_ohlcv(self, **kwargs):
        self.calls.append(kwargs)
        return [_record(kwargs["start"].replace(hour=0, minute=0, second=0, microsecond=0))]


def test_daily_fetch_aligns_only_start_and_preserves_same_day_horizon():
    client = _Client()
    provider = BinanceHistoricalOhlcvProvider(client=client)
    start = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
    end = datetime(2026, 9, 7, 18, 30, 12, tzinfo=UTC)

    provider.fetch_daily(asset="BTCUSDT", market="SPOT", start=start, end=end)

    assert len(client.calls) == 1
    assert client.calls[0]["start"] == start
    assert client.calls[0]["end"] == end
    assert client.calls[0]["start"] < client.calls[0]["end"]


def test_day_zero_minute_window_stops_on_completed_minute_grid():
    window = AdaptiveHistoricalReplayPlanner.minute_window_for_day(
        day=datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
        signal_source_time=datetime(2026, 9, 7, 17, 46, 9, tzinfo=UTC),
        now=datetime(2026, 9, 7, 18, 30, 45, tzinfo=UTC),
    )

    assert window is not None
    assert window.start == datetime(2026, 9, 7, 17, 46, 9, tzinfo=UTC)
    assert window.end == datetime(2026, 9, 7, 18, 30, tzinfo=UTC)
    assert window.limit == 43


def test_full_day_minute_window_requires_two_provider_pages():
    window = AdaptiveHistoricalReplayPlanner.minute_window_for_day(
        day=datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
        signal_source_time=datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
        now=datetime(2026, 9, 8, 0, 0, tzinfo=UTC),
    )

    assert window is not None
    assert window.limit == 1440
    assert AdaptiveHistoricalReplayPlanner.page_count(window) == 2
