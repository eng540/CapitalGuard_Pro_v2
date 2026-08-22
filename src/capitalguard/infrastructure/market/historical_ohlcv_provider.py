from __future__ import annotations

from datetime import datetime

from capitalguard.application.services.historical_market_replay_service import MarketCandle

from .binance_client import BinanceClient


class BinanceHistoricalOhlcvProvider:
    """Converts bounded Binance Klines to the Core historical replay contract."""

    def __init__(self, client: BinanceClient | None = None):
        self.client = client or BinanceClient()

    def fetch(
        self,
        *,
        asset: str,
        market: str | None,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int = 1500,
    ) -> tuple[list[MarketCandle], str]:
        market_kind = "FUTURES" if str(market or "").upper().startswith("FUTURES") else "SPOT"
        records = self.client.get_historical_ohlcv(
            symbol=asset,
            interval=interval,
            start=start,
            end=end,
            market=market_kind,
            limit=limit,
        )
        endpoint = records[0].provider_endpoint if records else ""
        return [
            MarketCandle(
                asset=record.symbol,
                market=market,
                open_time=record.open_time,
                open=record.open,
                high=record.high,
                low=record.low,
                close=record.close,
                volume=record.volume,
                data_source=f"BINANCE_{record.market}",
            )
            for record in records
        ], endpoint
