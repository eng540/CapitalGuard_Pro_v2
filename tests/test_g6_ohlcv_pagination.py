from datetime import datetime, timedelta, timezone
from decimal import Decimal

from capitalguard.infrastructure.market.historical_ohlcv_provider import BinanceHistoricalOhlcvProvider

UTC = timezone.utc


class Record:
    def __init__(self, ts):
        self.symbol = "BTCUSDT"
        self.market = "FUTURES"
        self.interval = "1m"
        self.open_time = ts
        self.open = Decimal("100")
        self.high = Decimal("101")
        self.low = Decimal("99")
        self.close = Decimal("100")
        self.volume = Decimal("1")
        self.provider_endpoint = "test"


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_historical_ohlcv(self, **kwargs):
        self.calls.append(kwargs)
        start = kwargs["start"]
        end = kwargs["end"]
        all_times = [datetime(2025, 12, 1, 0, 0, tzinfo=UTC) + timedelta(minutes=i) for i in range(1201)]
        eligible = [ts for ts in all_times if start <= ts <= end]
        return [Record(ts) for ts in eligible[: kwargs["limit"]]]


def test_provider_paginates_without_duplicate_candles():
    client = FakeClient()
    provider = BinanceHistoricalOhlcvProvider(client=client, max_pages=4)
    start = datetime(2025, 12, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=1200)
    candles, endpoint, coverage = provider.fetch_with_coverage(
        asset="BTCUSDT", market="FUTURES", interval="1m", start=start, end=end, limit=1000
    )
    assert len(candles) == 1201
    assert len({c.open_time for c in candles}) == 1201
    assert len(client.calls) >= 2
    assert coverage.actual_candles == 1200
    assert coverage.coverage_ratio == 1.0
    assert endpoint == "test"
