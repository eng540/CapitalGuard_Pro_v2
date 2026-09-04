from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class AmbiguityResolution:
    event: str
    resolution: str
    reason: str
    confidence: Decimal
    details: dict[str, Any]


class IntraCandleResolver:
    """Single-candle conflict resolver; it owns no replay state."""

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _hit(side, price, level):
        return price >= level if side.upper() == "LONG" else price <= level

    @staticmethod
    def _stop(side, price, level):
        return price <= level if side.upper() == "LONG" else price >= level

    @staticmethod
    def _trade_key(trade: dict) -> tuple:
        timestamp = trade.get("timestamp")
        trade_id = trade.get("trade_id")
        return timestamp, int(trade_id) if trade_id is not None else 0

    def resolve(self, *, symbol, market, side, candle_open, candle_close, stop, target_levels, candle_high, candle_low):
        start = candle_open.astimezone(timezone.utc)
        end = candle_close.astimezone(timezone.utc)
        try:
            trades = self.client.fetch_agg_trades(symbol=symbol, start=start, end=end, market=market or "SPOT", limit=1000)
        except Exception as exc:
            return self._fallback(side, stop, target_levels, start, candle_high, candle_low, f"AGG_TRADES_UNAVAILABLE:{type(exc).__name__}")

        ordered = sorted(
            (
                trade
                for trade in trades
                if isinstance(trade, dict)
                and isinstance(trade.get("timestamp"), datetime)
                and start <= trade["timestamp"].astimezone(timezone.utc) <= end
            ),
            key=self._trade_key,
        )
        for trade in ordered:
            price = Decimal(str(trade["price"]))
            hits = [i for i, level in target_levels if self._hit(side, price, level)]
            details = {
                "timestamp": trade["timestamp"].astimezone(timezone.utc).isoformat(),
                "price": str(price),
                "trade_id": trade.get("trade_id"),
                "target_indices": hits,
            }
            if self._stop(side, price, stop):
                return AmbiguityResolution("SL", "VERIFIED_EVENT", "AGG_TRADES", Decimal("1"), details)
            if hits:
                return AmbiguityResolution(f"TP{hits[0]}", "VERIFIED_EVENT", "AGG_TRADES", Decimal("1"), details)
        return self._fallback(side, stop, target_levels, start, candle_high, candle_low, "AGG_TRADES_EMPTY_OR_NO_TRIGGER")

    @staticmethod
    def _fallback(side, stop, target_levels, candle_open, high, low, reason):
        collided = [i for i, level in target_levels if (high >= level if side.upper() == "LONG" else low <= level)]
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
