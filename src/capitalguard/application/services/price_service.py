# --- START OF FINAL, CORRECTED AND ENHANCED FILE: src/capitalguard/application/services/price_service.py ---
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
price_cache = InMemoryCache(ttl_seconds=60)


@dataclass
class PriceService:
    """
    Price fetching service with a small cache and pluggable providers.
    """

    async def get_cached_price(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        """
        Async: Return cached price if available; otherwise fetch from provider and cache it.
        
        Args:
            symbol (str): The trading symbol (e.g., "BTCUSDT").
            market (str): The market type (e.g., "Futures").
            force_refresh (bool): If True, bypasses the cache and fetches a fresh price.
        """
        provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        cache_key = f"price:{provider}:{(market or 'spot').lower()}:{symbol.upper()}"

        # ✅ MODIFICATION: Only check the cache if force_refresh is False.
        if not force_refresh:
            cached_price = price_cache.get(cache_key)
            if cached_price is not None:
                return cached_price

        live_price: Optional[float] = None

        if provider == "binance":
            is_spot = str(market or "Spot").lower().startswith("spot")
            loop = asyncio.get_running_loop()
            live_price = await loop.run_in_executor(None, BinancePricing.get_price, symbol, is_spot)

        elif provider == "coingecko":
            cg_client = CoinGeckoClient()
            live_price = await cg_client.get_price(symbol)

        else:
            log.error("Unknown market data provider: %s", provider)
            return None

        if live_price is not None:
            ttl = 30 if provider == "coingecko" else 60
            price_cache.set(cache_key, live_price, ttl_seconds=ttl)

        return live_price

    # -------- Sync bridges --------

    def get_cached_price_blocking(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        """
        Sync (blocking): Safe to call ONLY when no event loop is running.
        """
        try:
            asyncio.get_running_loop()
            raise RuntimeError(
                "get_cached_price_blocking() was called from within a running event loop. "
                "Use: `await price_service.get_cached_price(...)` in async code."
            )
        except RuntimeError as e:
            # Re-raise the specific error we created, but let other RuntimeErrors pass
            if "get_cached_price_blocking() was called" in str(e):
                raise e
            # No running loop → safe to create and run one.
            return asyncio.run(self.get_cached_price(symbol, market, force_refresh))

    # Backward-compatible aliases
    async def get_preview_price(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        return await self.get_cached_price(symbol, market, force_refresh)

    def get_preview_price_blocking(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        return self.get_cached_price_blocking(symbol, market, force_refresh)
# --- END OF FINAL, CORRECTED AND ENHANCED FILE ---```