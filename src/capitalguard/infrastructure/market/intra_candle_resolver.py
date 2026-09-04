from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable


@dataclass(frozen=True)
class AmbiguityResolution:
    event: str
    resolution: str
    reason: str
    confidence: Decimal
    details: dict[str, Any]


class IntraCandleResolver:
    """Resolve a single OHLCV collision without owning replay state."""

    def __init__(self, client) -> None:
        self.client = client

    @staticmethod
    def _hit(side: str, price: Decimal, level: Decimal) -> bool:
        return price >= level if side.upper() == "LONG" else price <= level

    @staticmethod
    def _stop(side: str, price: Decimal, level: Decimal) -> bool:
        return price <= level if side.upper() == "LONG" else price >= level

    @staticmethod
    def _sorted_trades(trades: Iterable[dict], start: datetime, end: datetime) -> list[dict]:
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        valid: list[dict] = []
        for trade in trades:
            timestamp = trade.get("timestamp")
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                continue
            timestamp = timestamp.astimezone(timezone.utc)
            if not start_utc <= timestamp <= end_utc:
                continue
            price = Decimal(str(trade.get("price")))
            if not price.is_finite() or price <= 0:
                continue
            valid.append({**trade, "timestamp": timestamp, "price": price})
        return sorted(valid, key=lambda item: (item["timestamp"], str(item.get("trade_id") or "")))

    def resolve(
        self,
        *,
        symbol: str,
        market: str | None,
        side: str,
        candle_open: datetime,
        candle_close: datetime,
        stop: Decimal,
        target_levels: list[tuple[int, Decimal]],
        candle_high: Decimal,
        candle_low: Decimal,
    ) -> AmbiguityResolution:
        start = candle_open.astimezone(timezone.utc)
        end = candle_close.astimezone(timezone.utc)
        try:
            raw_trades = self.client.fetch_agg_trades(
                symbol=symbol,
                start=start,
                end=end,
                market=market or "SPOT",
                limit=1000,
            )
        except Exception as exc:
            return self._fallback(
                side,
                stop,
                target_levels,
                start,
                candle_high,
                candle_low,
                f"AGG_TRADES_UNAVAILABLE:{type(exc).__name__}",
            )

        trades = self._sorted_trades(raw_trades or [], start, end)
        for trade in trades:
            price = trade["price"]
            hits = [index for index, level in target_levels if self._hit(side, price, level)]
            stop_hit = self._stop(side, price, stop)
            if stop_hit:
                return AmbiguityResolution(
                    "SL",
                    "VERIFIED_EVENT",
                    "AGG_TRADES",
                    Decimal("1"),
                    {
                        "timestamp": trade["timestamp"].isoformat(),
                        "price": str(price),
                        "target_indices": hits,
                        "trade_id": trade.get("trade_id"),
                    },
                )
            if hits:
                return AmbiguityResolution(
                    f"TP{hits[0]}",
                    "VERIFIED_EVENT",
                    "AGG_TRADES",
                    Decimal("1"),
                    {
                        "timestamp": trade["timestamp"].isoformat(),
                        "price": str(price),
                        "target_indices": hits,
                        "trade_id": trade.get("trade_id"),
                    },
                )

        return self._fallback(
            side,
            stop,
            target_levels,
            start,
            candle_high,
            candle_low,
            "AGG_TRADES_EMPTY_OR_NO_TRIGGER",
        )

    @staticmethod
    def _fallback(
        side: str,
        stop: Decimal,
        target_levels: list[tuple[int, Decimal]],
        candle_open: datetime,
        high: Decimal,
        low: Decimal,
        reason: str,
    ) -> AmbiguityResolution:
        collided = [
            index
            for index, level in target_levels
            if high >= level if side.upper() == "LONG"
        ] if side.upper() == "LONG" else [
            index
            for index, level in target_levels
            if low <= level
        ]
        return AmbiguityResolution(
            "SL",
            "PESSIMISTIC_FALLBACK",
            reason,
            Decimal("0.5000"),
            {
                "candle_time": candle_open.isoformat(),
                "high": str(high),
                "low": str(low),
                "stop": str(stop),
                "collided_target_indices": collided,
                "inferred_event": "SL_FIRST",
            },
        )
