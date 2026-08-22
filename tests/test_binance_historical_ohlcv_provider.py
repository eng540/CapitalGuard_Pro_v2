from datetime import datetime, timezone
from decimal import Decimal

import pytest

from capitalguard.infrastructure.market.binance_client import BinanceClient, HistoricalMarketProviderError, SPOT_KLINES


def test_binance_historical_ohlcv_requests_bounded_spot_candles(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [[1_700_000_000_000, "100", "110", "90", "105", "12", 0]]

    def fake_get(url, params, timeout):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return Response()

    monkeypatch.setattr("capitalguard.infrastructure.market.binance_client.requests.get", fake_get)
    candles = BinanceClient().get_historical_ohlcv(
        symbol="btcusdt", interval="1m", market="spot", limit=1,
        start=datetime(2023, 11, 14, 22, 0, tzinfo=timezone.utc),
        end=datetime(2023, 11, 14, 23, 0, tzinfo=timezone.utc),
    )

    assert captured["url"] == SPOT_KLINES
    assert captured["params"]["symbol"] == "BTCUSDT"
    assert captured["params"]["limit"] == 1
    assert candles[0].close == Decimal("105")
    assert candles[0].provider_endpoint == SPOT_KLINES


@pytest.mark.parametrize("symbol, interval, limit", [("bad symbol", "1m", 1), ("BTCUSDT", "2s", 1), ("BTCUSDT", "1m", 1501)])
def test_binance_historical_ohlcv_rejects_invalid_bounds_before_request(symbol, interval, limit):
    with pytest.raises(HistoricalMarketProviderError):
        BinanceClient().get_historical_ohlcv(
            symbol=symbol, interval=interval, limit=limit,
            start=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end=datetime(2023, 1, 2, tzinfo=timezone.utc),
        )
