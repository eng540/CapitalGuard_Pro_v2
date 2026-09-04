from datetime import datetime, timedelta, timezone
from decimal import Decimal

from capitalguard.domain.coverage import CoverageStatus
from capitalguard.infrastructure.market.binance_client import BinanceHistoricalCandle
from capitalguard.infrastructure.market.historical_ohlcv_provider import BinanceHistoricalOhlcvProvider

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record(index: int) -> BinanceHistoricalCandle:
    timestamp = START + timedelta(minutes=index)
    return BinanceHistoricalCandle(symbol="BTCUSDT", market="SPOT", interval="1m", open_time=timestamp, open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"), volume=Decimal("1"), provider_endpoint="test://binance/klines")


class PaginatedFakeClient:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def get_historical_ohlcv(self, *, symbol, interval, start, end, market, limit):
        self.calls.append({"start": start, "end": end, "limit": limit})
        eligible = [item for item in self.records if start <= item.open_time <= end]
        return eligible[:limit]


def test_provider_paginates_more_than_one_thousand_candles_without_duplicates():
    records = [_record(index) for index in range(1001)]
    client = PaginatedFakeClient(records)
    provider = BinanceHistoricalOhlcvProvider(client=client, max_pages=3)
    candles, endpoint, coverage = provider.fetch_with_coverage(asset="BTCUSDT", market="SPOT", interval="1m", start=START, end=START + timedelta(minutes=1000), limit=1500)
    assert len(client.calls) == 2
    assert all(call["limit"] == 1000 for call in client.calls)
    assert len(candles) == 1001
    assert len({candle.open_time for candle in candles}) == 1001
    assert endpoint == "test://binance/klines"
    assert coverage.status is CoverageStatus.FULL
    assert coverage.coverage_ratio == 1.0


def test_provider_does_not_treat_short_page_as_completion():
    records = [_record(index) for index in range(1200)]
    client = PaginatedFakeClient(records)
    provider = BinanceHistoricalOhlcvProvider(client=client, max_pages=3)
    candles, _endpoint, coverage = provider.fetch_with_coverage(asset="BTCUSDT", market="SPOT", interval="1m", start=START, end=START + timedelta(minutes=1199), limit=1500)
    assert len(client.calls) == 2
    assert len(candles) == 1200
    assert coverage.status is CoverageStatus.FULL


def test_provider_preserves_partial_window_when_source_stops_early():
    records = [_record(index) for index in range(500)]
    client = PaginatedFakeClient(records)
    provider = BinanceHistoricalOhlcvProvider(client=client, max_pages=3)
    candles, _endpoint, coverage = provider.fetch_with_coverage(asset="BTCUSDT", market="SPOT", interval="1m", start=START, end=START + timedelta(minutes=1000), limit=1500)
    assert len(candles) == 500
    assert coverage.status is CoverageStatus.PARTIAL_WINDOW
    assert coverage.actual_end == START + timedelta(minutes=499)
    assert coverage.coverage_ratio == 500 / 1000
