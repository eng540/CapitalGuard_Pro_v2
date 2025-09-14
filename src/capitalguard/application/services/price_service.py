#START src/capitalguard/application/services/price_service.py
import logging
import os
import asyncio
from dataclasses import dataclass
from typing import Optional

from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient
from capitalguard.infrastructure.cache import InMemoryCache

log = logging.getLogger(__name__)

# We instantiate the cache here to be used by the service instance
price_cache = InMemoryCache(ttl_seconds=60)

@dataclass
class PriceService:
    """
    A service layer for fetching prices that can adapt its source.
    It uses a caching layer to prevent rate limiting and can switch
    between Binance and CoinGecko based on the environment configuration.
    It provides both async and sync methods for fetching prices.
    """

    async def get_cached_price(self, symbol: str, market: str) -> Optional[float]:
        """
        (Async) Gets the price for a symbol, preferring a cached value.
        If not cached, it fetches from the configured provider and caches the result.
        """
        provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        cache_key = f"price:{provider}:{market.lower()}:{symbol.upper()}"
        
        cached_price = price_cache.get(cache_key)
        if cached_price is not None:
            return cached_price
            
        live_price: Optional[float] = None
        if provider == "binance":
            is_spot = (str(market or "Spot").lower().startswith("spot"))
            # Binance client is synchronous, so we run it in a thread pool
            loop = asyncio.get_running_loop()
            live_price = await loop.run_in_executor(
                None, BinancePricing.get_price, symbol, is_spot
            )
        elif provider == "coingecko":
            cg_client = CoinGeckoClient()
            live_price = await cg_client.get_price(symbol)
        else:
            log.error(f"Unknown market data provider: {provider}")
            return None
        
        if live_price is not None:
            ttl = 30 if provider == "coingecko" else 60
            price_cache.set(cache_key, live_price, ttl_seconds=ttl)
            
        return live_price

    def get_cached_price_sync(self, symbol: str, market: str) -> Optional[float]:
        """
        (Sync Wrapper) Gets the price for a symbol.
        This is a synchronous bridge to the async get_cached_price method,
        making it easy to call from synchronous code like keyboards.py or alert_service.py.
        """
        try:
            # If an event loop is already running, use it to run the async function
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self.get_cached_price(symbol, market))
        except RuntimeError:
            # If no event loop is running, create a new one
            return asyncio.run(self.get_cached_price(symbol, market))

    async def get_preview_price(self, symbol: str, market: str) -> Optional[float]:
        """(Async) Alias for the async cached version for consistency in async contexts."""
        return await self.get_cached_price(symbol, market)

    def get_preview_price_sync(self, symbol: str, market: str) -> Optional[float]:
        """(Sync) Alias for the sync cached version for consistency in sync contexts."""
        return self.get_cached_price_sync(symbol, market)
#END