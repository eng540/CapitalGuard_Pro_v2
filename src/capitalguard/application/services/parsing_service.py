# --- START OF FINAL, HARDENED, AND PRODUCTION-READY FILE (Version 16.3.1 - Concurrency Fix) ---
# src/capitalguard/application/services/price_service.py
import logging
import os
import asyncio
from dataclasses import dataclass
from typing import Optional

from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient
from capitalguard.infrastructure.cache import InMemoryCache

log = logging.getLogger(__name__)

# Cache instance for short-lived price caching
[cite_start]price_cache = InMemoryCache(ttl_seconds=60) [cite: 197]


@dataclass
class PriceService:
    """
    [cite_start]Price fetching service with a small cache and pluggable providers. [cite: 198]
    """

    async def get_cached_price(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        """
        [cite_start]Async: Return cached price if available; [cite: 199]
        otherwise fetch from provider and cache it.
        
        Args:
            [cite_start]symbol (str): The trading symbol (e.g., "BTCUSDT"). [cite: 199]
            [cite_start]market (str): The market type (e.g., "Futures"). [cite: 200]
            [cite_start]force_refresh (bool): If True, bypasses the cache and fetches a fresh price. [cite: 200]
        """
        provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        cache_key = f"price:{provider}:{(market or 'spot').lower()}:{symbol.upper()}"

        if not force_refresh:
            cached_price = price_cache.get(cache_key)
            if cached_price is not None:
                return cached_price

        live_price: Optional[float] = None

        [cite_start]if provider == "binance": [cite: 202]
            is_spot = str(market or "Spot").lower().startswith("spot")
            # BinancePricing.get_price is a static method, safe for run_in_executor
            loop = asyncio.get_running_loop()
            [cite_start]live_price = await loop.run_in_executor(None, BinancePricing.get_price, symbol, is_spot) [cite: 202]

        elif provider == "coingecko":
            cg_client = CoinGeckoClient()
            [cite_start]live_price = await cg_client.get_price(symbol) [cite: 203]

        else:
            log.error("Unknown market data provider: %s", provider)
            return None

        if live_price is not None:
            ttl = 30 if provider == "coingecko" else 60
            [cite_start]price_cache.set(cache_key, live_price, ttl_seconds=ttl) [cite: 203]

        [cite_start]return live_price [cite: 204]

    # ❌ THE FIX: The unsafe blocking function using asyncio.run is REMOVED to prevent event loop crashes.
    # def get_cached_price_blocking(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
    #     ...

    # Backward-compatible aliases (Blocking aliases removed, only async remain)
    async def get_preview_price(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        return await self.get_cached_price(symbol, market, force_refresh)

# --- END OF FINAL, HARDENED, AND PRODUCTION-READY FILE (Version 16.3.1 - Concurrency Fix) ---