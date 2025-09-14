#START src/capitalguard/application/services/price_service.py
import logging
import os
from dataclasses import dataclass
from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient
from capitalguard.infrastructure.cache import price_cache

log = logging.getLogger(__name__)

@dataclass
class PriceService:
    """
    A service layer for fetching prices that can adapt its source.
    It uses a caching layer to prevent rate limiting and can switch
    between Binance and CoinGecko based on the environment configuration.
    """

    async def get_cached_price(self, symbol: str, market: str) -> float | None:
        """
        Gets the price for a symbol, preferring a cached value if available.
        If the price is not in the cache, it fetches from the configured
        provider (Binance or CoinGecko) and caches it.
        """
        provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        cache_key = f"price:{provider}:{market.lower()}:{symbol.upper()}"
        
        # 1. Try to get from cache first
        cached_price = price_cache.get(cache_key)
        if cached_price is not None:
            return cached_price
            
        # 2. If not in cache, fetch from the actual provider
        live_price = None
        if provider == "binance":
            is_spot = (str(market or "Spot").lower().startswith("spot"))
            live_price = BinancePricing.get_price(symbol, spot=is_spot)
        elif provider == "coingecko":
            cg_client = CoinGeckoClient()
            live_price = await cg_client.get_price(symbol)
        else:
            log.error(f"Unknown market data provider: {provider}")
            return None
        
        # 3. If fetched successfully, store it in the cache for next time
        if live_price is not None:
            # Use a shorter TTL for CoinGecko as its API has stricter rate limits
            ttl = 30 if provider == "coingecko" else 60
            price_cache.set(cache_key, live_price, ttl_seconds=ttl)
            
        return live_price

    async def get_preview_price(self, symbol: str, market: str) -> float | None:
        """This method now acts as an alias for the cached version for consistency."""
        return await self.get_cached_price(symbol, market)
#END