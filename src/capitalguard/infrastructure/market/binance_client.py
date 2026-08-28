from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests


BASE = "https://api.binance.com/api/v3"
SPOT_KLINES = f"{BASE}/klines"
FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"
SUPPORTED_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}
MAX_KLINES_LIMIT = 1500
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class HistoricalMarketProviderError(RuntimeError):
    """Raised when a historical candle source is unavailable or violates its contract."""


@dataclass(frozen=True)
class BinanceHistoricalCandle:
    symbol: str
    market: str
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    provider_endpoint: str


class BinanceClient:
    def get_price(self, symbol: str) -> float:
        r = requests.get(f"{BASE}/ticker/price", params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data["price"])

    @staticmethod
    def _validate_historical_request(symbol: str, interval: str, start: datetime, end: datetime, limit: int) -> tuple[str, str, datetime, datetime]:
        normalized_symbol = symbol.strip().upper()
        normalized_interval = interval.strip()
        if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
            raise HistoricalMarketProviderError("Historical candle symbol is invalid")
        if normalized_interval not in SUPPORTED_INTERVALS:
            raise HistoricalMarketProviderError("Historical candle interval is unsupported")
        if start.tzinfo is None or end.tzinfo is None:
            raise HistoricalMarketProviderError("Historical candle bounds must be timezone-aware")
        start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        if start_utc >= end_utc:
            raise HistoricalMarketProviderError("Historical candle bounds are invalid")
        if not 1 <= limit <= MAX_KLINES_LIMIT:
            raise HistoricalMarketProviderError("Historical candle limit is out of range")
        return normalized_symbol, normalized_interval, start_utc, end_utc

    @staticmethod
    def _positive_decimal(value: object) -> Decimal:
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise HistoricalMarketProviderError("Historical candle contains invalid numeric value") from exc
        if not decimal.is_finite() or decimal <= 0:
            raise HistoricalMarketProviderError("Historical candle contains non-positive numeric value")
        return decimal

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(30.0, (0.5 * (2**attempt)) + random.uniform(0.0, 0.25))

    def get_historical_ohlcv(
        self,
        *,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        market: str = "SPOT",
        limit: int = MAX_KLINES_LIMIT,
        timeout_seconds: float = 10.0,
        max_retries: int = 4,
    ) -> list[BinanceHistoricalCandle]:
        normalized_symbol, normalized_interval, start_utc, end_utc = self._validate_historical_request(symbol, interval, start, end, limit)
        normalized_market = market.strip().upper()
        if normalized_market not in {"SPOT", "FUTURES"}:
            raise HistoricalMarketProviderError("Historical candle market is unsupported")
        if not 0.1 <= timeout_seconds <= 30:
            raise HistoricalMarketProviderError("Historical candle timeout is out of range")
        if not 0 <= max_retries <= 8:
            raise HistoricalMarketProviderError("Historical candle max_retries is out of range")

        endpoint = FUTURES_KLINES if normalized_market == "FUTURES" else SPOT_KLINES
        params = {
            "symbol": normalized_symbol,
            "interval": normalized_interval,
            "startTime": int(start_utc.timestamp() * 1000),
            "endTime": int(end_utc.timestamp() * 1000),
            "limit": limit,
        }

        response = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.get(endpoint, params=params, timeout=timeout_seconds)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt >= max_retries:
                        response.raise_for_status()
                    time.sleep(self._retry_delay(attempt, response.headers.get("Retry-After")))
                    continue
                response.raise_for_status()
                rows = response.json()
                break
            except requests.RequestException as exc:
                if attempt >= max_retries:
                    raise HistoricalMarketProviderError("Historical candle provider is unavailable") from exc
                time.sleep(self._retry_delay(attempt))
            except ValueError as exc:
                raise HistoricalMarketProviderError("Historical candle provider returned invalid JSON") from exc
        else:
            raise HistoricalMarketProviderError("Historical candle provider is unavailable")

        if response is None:
            raise HistoricalMarketProviderError("Historical candle provider is unavailable")
        if not isinstance(rows, list) or len(rows) > limit:
            raise HistoricalMarketProviderError("Historical candle provider returned invalid payload")

        candles: list[BinanceHistoricalCandle] = []
        seen: set[datetime] = set()
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                raise HistoricalMarketProviderError("Historical candle provider returned malformed candle")
            try:
                open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
            except (TypeError, ValueError, OverflowError) as exc:
                raise HistoricalMarketProviderError("Historical candle timestamp is invalid") from exc
            if not start_utc <= open_time <= end_utc or open_time in seen:
                raise HistoricalMarketProviderError("Historical candle range is inconsistent")
            seen.add(open_time)
            candles.append(
                BinanceHistoricalCandle(
                    symbol=normalized_symbol,
                    market=normalized_market,
                    interval=normalized_interval,
                    open_time=open_time,
                    open=self._positive_decimal(row[1]),
                    high=self._positive_decimal(row[2]),
                    low=self._positive_decimal(row[3]),
                    close=self._positive_decimal(row[4]),
                    volume=self._positive_decimal(row[5]),
                    provider_endpoint=endpoint,
                )
            )
        return sorted(candles, key=lambda candle: candle.open_time)
