from __future__ import annotations

from datetime import datetime, timedelta, timezone

from capitalguard.application.services.historical_market_replay_service import MarketCandle
from capitalguard.domain.coverage import HistoricalCoverage, calculate_historical_coverage, interval_delta

from .binance_client import BinanceClient, MAX_KLINES_LIMIT


PROVIDER_PAGE_LIMIT = 1000
DEFAULT_MAX_PAGES = 1000


class BinanceHistoricalOhlcvProvider:
    """Canonical historical OHLCV acquisition with pagination and coverage evidence."""

    def __init__(self, client: BinanceClient | None = None, *, max_pages: int = DEFAULT_MAX_PAGES):
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self.client = client or BinanceClient()
        self.max_pages = max_pages

    @staticmethod
    def _normalize_bounds(start: datetime, end: datetime) -> tuple[datetime, datetime]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Historical bounds must be timezone-aware")
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        if start_utc >= end_utc:
            raise ValueError("Historical bounds are invalid")
        return start_utc, end_utc

    def fetch_with_coverage(
        self,
        *,
        asset: str,
        market: str | None,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int = MAX_KLINES_LIMIT,
    ) -> tuple[list[MarketCandle], str, HistoricalCoverage]:
        start_utc, end_utc = self._normalize_bounds(start, end)
        interval_duration = interval_delta(interval)
        request_limit = min(max(1, int(limit)), PROVIDER_PAGE_LIMIT)
        market_kind = "FUTURES" if str(market or "").upper().startswith("FUTURES") else "SPOT"

        candles_by_time: dict[datetime, MarketCandle] = {}
        endpoint = ""
        cursor = start_utc

        for _ in range(self.max_pages):
            if cursor >= end_utc:
                break
            records = self.client.get_historical_ohlcv(
                symbol=asset,
                interval=interval,
                start=cursor,
                end=end_utc,
                market=market_kind,
                limit=request_limit,
            )
            if not records:
                break

            for record in records:
                candle = MarketCandle(
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
                if start_utc <= record.open_time < end_utc:
                    candles_by_time[record.open_time] = candle
                endpoint = record.provider_endpoint

            last_open = records[-1].open_time
            next_cursor = last_open + interval_duration
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if last_open >= end_utc - interval_duration:
                break

        candles = sorted(candles_by_time.values(), key=lambda candle: candle.open_time)
        coverage = calculate_historical_coverage(
            requested_start=start_utc,
            requested_end=end_utc,
            candle_times=(candle.open_time for candle in candles),
            interval=interval_duration,
        )
        return candles, endpoint, coverage

    def fetch_daily(
        self,
        *,
        asset: str,
        market: str | None,
        start: datetime,
        end: datetime,
        limit: int = 365,
    ) -> tuple[list[MarketCandle], str, HistoricalCoverage]:
        """Thin semantic adapter for G6 annual macro traversal; reuses fetch_with_coverage()."""
        return self.fetch_with_coverage(
            asset=asset,
            market=market,
            interval="1d",
            start=start,
            end=end,
            limit=min(max(1, int(limit)), 365),
        )

    def fetch_minute_day(
        self,
        *,
        asset: str,
        market: str | None,
        start: datetime,
        end: datetime,
    ) -> tuple[list[MarketCandle], str, HistoricalCoverage]:
        """Fetch exactly one bounded critical-day minute window; never targets the next day."""
        start_utc, end_utc = self._normalize_bounds(start, end)
        day_end = start_utc.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        bounded_end = min(end_utc, day_end)
        return self.fetch_with_coverage(
            asset=asset,
            market=market,
            interval="1m",
            start=start_utc,
            end=bounded_end,
            limit=PROVIDER_PAGE_LIMIT,
        )

    def fetch(
        self,
        *,
        asset: str,
        market: str | None,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int = MAX_KLINES_LIMIT,
    ) -> tuple[list[MarketCandle], str]:
        """Backward-compatible G6 provider contract; coverage is available via fetch_with_coverage."""
        candles, endpoint, _coverage = self.fetch_with_coverage(
            asset=asset,
            market=market,
            interval=interval,
            start=start,
            end=end,
            limit=limit,
        )
        return candles, endpoint
