from datetime import datetime, timedelta, timezone
from decimal import Decimal

from capitalguard.infrastructure.market.intra_candle_resolver import IntraCandleResolver

UTC = timezone.utc
BASE = datetime(2025, 12, 3, 18, 12, tzinfo=UTC)


class FakeClient:
    def __init__(self, trades):
        self.trades = trades
        self.calls = []

    def fetch_agg_trades(self, **kwargs):
        self.calls.append(kwargs)
        return self.trades


def test_resolver_uses_first_trade_that_hits_a_level():
    client = FakeClient([
        {"timestamp": BASE + timedelta(seconds=20), "price": Decimal("93000"), "trade_id": 2},
        {"timestamp": BASE + timedelta(seconds=10), "price": Decimal("93400"), "trade_id": 1},
    ])
    result = IntraCandleResolver(client).resolve(
        symbol="BTCUSDT", market="FUTURES", side="LONG",
        candle_open=BASE, candle_close=BASE + timedelta(minutes=1),
        stop=Decimal("92000"), target_levels=[(1, Decimal("93400"))],
        candle_high=Decimal("93450"), candle_low=Decimal("91980"),
    )
    assert result.event == "TP1"
    assert result.resolution == "VERIFIED_EVENT"
    assert result.details["timestamp"] == (BASE + timedelta(seconds=10)).isoformat()
    assert len(client.calls) == 1


def test_resolver_falls_back_to_pessimistic_sl_first_when_trades_unavailable():
    class FailingClient:
        def fetch_agg_trades(self, **kwargs):
            raise RuntimeError("archive unavailable")

    result = IntraCandleResolver(FailingClient()).resolve(
        symbol="BTCUSDT", market="FUTURES", side="LONG",
        candle_open=BASE, candle_close=BASE + timedelta(minutes=1),
        stop=Decimal("92000"), target_levels=[(1, Decimal("93400"))],
        candle_high=Decimal("93450"), candle_low=Decimal("91980"),
    )
    assert result.event == "SL"
    assert result.resolution == "PESSIMISTIC_FALLBACK"
    assert result.details["inferred_event"] == "SL_FIRST"


def test_resolver_discards_trades_outside_disputed_candle():
    client = FakeClient([
        {"timestamp": BASE - timedelta(seconds=1), "price": Decimal("93400"), "trade_id": 1},
        {"timestamp": BASE + timedelta(seconds=30), "price": Decimal("93400"), "trade_id": 2},
    ])
    result = IntraCandleResolver(client).resolve(
        symbol="BTCUSDT", market="FUTURES", side="LONG",
        candle_open=BASE, candle_close=BASE + timedelta(minutes=1),
        stop=Decimal("92000"), target_levels=[(1, Decimal("93400"))],
        candle_high=Decimal("93450"), candle_low=Decimal("91980"),
    )
    assert result.event == "TP1"
    assert result.details["trade_id"] == 2
