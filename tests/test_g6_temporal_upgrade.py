from datetime import datetime, timedelta
from decimal import Decimal

from capitalguard.domain.coverage import CoverageStatus, calculate_historical_coverage, interval_delta
from capitalguard.infrastructure.market.intra_candle_resolver import IntraCandleResolver


def u(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_source_seconds_do_not_break_market_grid():
    start = u("2025-12-04T20:38:30Z")
    end = start + timedelta(minutes=1440)
    times = [start.replace(second=0) + timedelta(minutes=i) for i in range(1440)]
    coverage = calculate_historical_coverage(
        requested_start=start,
        requested_end=end,
        candle_times=times,
        interval=interval_delta("1m"),
    )
    assert coverage.status is CoverageStatus.FULL
    assert coverage.actual_candles == 1440
    assert coverage.expected_candles == 1440


def test_real_missing_minute_is_gapped():
    start = u("2025-12-04T20:38:00Z")
    end = start + timedelta(minutes=10)
    times = [start + timedelta(minutes=i) for i in range(10) if i != 5]
    coverage = calculate_historical_coverage(
        requested_start=start,
        requested_end=end,
        candle_times=times,
        interval=interval_delta("1m"),
    )
    assert coverage.status is CoverageStatus.GAPPED
    assert coverage.missing_candles == 1


def test_agg_trades_resolve_stop_before_target():
    class Client:
        def fetch_agg_trades(self, **kwargs):
            return [
                {"timestamp": u("2025-12-04T20:38:10Z"), "price": Decimal("92000"), "trade_id": 1},
                {"timestamp": u("2025-12-04T20:38:40Z"), "price": Decimal("93400"), "trade_id": 2},
            ]

    result = IntraCandleResolver(Client()).resolve(
        symbol="BTCUSDT",
        market="FUTURES",
        side="LONG",
        candle_open=u("2025-12-04T20:38:00Z"),
        candle_close=u("2025-12-04T20:39:00Z"),
        stop=Decimal("92000"),
        target_levels=[(1, Decimal("93400"))],
        candle_high=Decimal("93450"),
        candle_low=Decimal("91980"),
    )
    assert result.event == "SL"
    assert result.resolution == "VERIFIED_EVENT"


def test_agg_trades_unavailable_uses_conservative_fallback():
    class Client:
        def fetch_agg_trades(self, **kwargs):
            raise RuntimeError("historical data unavailable")

    result = IntraCandleResolver(Client()).resolve(
        symbol="BTCUSDT",
        market="FUTURES",
        side="LONG",
        candle_open=u("2025-12-04T20:38:00Z"),
        candle_close=u("2025-12-04T20:39:00Z"),
        stop=Decimal("92000"),
        target_levels=[(1, Decimal("93400"))],
        candle_high=Decimal("93450"),
        candle_low=Decimal("91980"),
    )
    assert result.event == "SL"
    assert result.resolution == "PESSIMISTIC_FALLBACK"
