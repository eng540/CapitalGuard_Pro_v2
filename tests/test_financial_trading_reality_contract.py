from datetime import datetime, timezone
from decimal import Decimal

from capitalguard.domain.financial_metrics import price_return_pct
from capitalguard.infrastructure.market.intra_candle_resolver import IntraCandleResolver


def test_c02_canonical_pnl_examples_and_direction_invariants():
    assert price_return_pct(100, 110, "LONG") == Decimal("10")
    assert price_return_pct(100, 90, "LONG") == Decimal("-10")
    assert price_return_pct(100, 90, "SHORT") == Decimal("10")
    assert price_return_pct(100, 110, "SHORT") == Decimal("-10")


def test_c06_c07_ambiguous_ohlc_is_unverifiable():
    class Client:
        def fetch_agg_trades(self, **kwargs):
            raise RuntimeError("historical data unavailable")

    result = IntraCandleResolver(Client()).resolve(
        symbol="BTCUSDT",
        market="FUTURES",
        side="LONG",
        candle_open=datetime(2025, 12, 4, 20, 38, tzinfo=timezone.utc),
        candle_close=datetime(2025, 12, 4, 20, 39, tzinfo=timezone.utc),
        stop=Decimal("80"),
        target_levels=[(1, Decimal("110"))],
        candle_high=Decimal("115"),
        candle_low=Decimal("75"),
    )
    assert result.event == "AMBIGUOUS"
    assert result.resolution == "UNVERIFIABLE"
    assert "SL_FIRST" not in str(result.details)
    assert set(result.details["possible_events"]) == {"SL", "TP1"}


def test_c06_fine_grain_evidence_can_prove_chronology():
    class Client:
        def fetch_agg_trades(self, **kwargs):
            return [{
                "timestamp": datetime(2025, 12, 4, 20, 38, 10, tzinfo=timezone.utc),
                "price": "75",
                "trade_id": 1,
            }]

    result = IntraCandleResolver(Client()).resolve(
        symbol="BTCUSDT",
        market="FUTURES",
        side="LONG",
        candle_open=datetime(2025, 12, 4, 20, 38, tzinfo=timezone.utc),
        candle_close=datetime(2025, 12, 4, 20, 39, tzinfo=timezone.utc),
        stop=Decimal("80"),
        target_levels=[(1, Decimal("110"))],
        candle_high=Decimal("115"),
        candle_low=Decimal("75"),
    )
    assert result.event == "SL"
    assert result.resolution == "VERIFIED_EVENT"
