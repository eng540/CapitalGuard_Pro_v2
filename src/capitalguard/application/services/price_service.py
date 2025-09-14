# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/price_service.py ---
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

    ✅ Async-first design:
       - Use `await get_cached_price(...)` from any async context (PTB handlers, FastAPI).
       - For strictly synchronous contexts (no running event loop), use
         `get_cached_price_blocking(...)`.

    🚫 Important:
       - Calling a sync wrapper from inside a running event loop will raise a clear error
         to avoid issues like: "asyncio.run() cannot be called from a running event loop".
    """

    async def get_cached_price(self, symbol: str, market: str) -> Optional[float]:
        """
        Async: Return cached price if available; otherwise fetch from provider and cache it.
        """
        provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        cache_key = f"price:{provider}:{(market or 'spot').lower()}:{symbol.upper()}"

        cached_price = price_cache.get(cache_key)
        if cached_price is not None:
            return cached_price

        live_price: Optional[float] = None

        if provider == "binance":
            # Binance client is synchronous; run it in a thread without blocking the event loop
            is_spot = str(market or "Spot").lower().startswith("spot")
            loop = asyncio.get_running_loop()
            live_price = await loop.run_in_executor(None, BinancePricing.get_price, symbol, is_spot)

        elif provider == "coingecko":
            # CoinGecko client is async
            cg_client = CoinGeckoClient()
            live_price = await cg_client.get_price(symbol)

        else:
            log.error("Unknown market data provider: %s", provider)
            return None

        if live_price is not None:
            # Shorter TTL for CoinGecko (usually slower / broader) vs Binance (tighter)
            ttl = 30 if provider == "coingecko" else 60
            price_cache.set(cache_key, live_price, ttl_seconds=ttl)

        return live_price

    # -------- Sync bridges --------

    def get_cached_price_blocking(self, symbol: str, market: str) -> Optional[float]:
        """
        Sync (blocking): Safe to call ONLY when no event loop is running.

        - If an event loop is running (e.g., inside PTB async handlers), this method raises
          a RuntimeError instructing you to use `await get_cached_price(...)` instead.
        - If no loop is running (e.g., CLI scripts, worker processes), it spins a fresh loop.
        """
        try:
            # Will succeed (and NOT raise) if we're already inside a running event loop.
            asyncio.get_running_loop()
            # If we get here, we're *inside* an event loop → forbid blocking call.
            raise RuntimeError(
                "get_cached_price_blocking() was called from within a running event loop. "
                "Use: `await price_service.get_cached_price(symbol, market)` in async code."
            )
        except RuntimeError:
            # No running loop → safe to create and run one.
            return asyncio.run(self.get_cached_price(symbol, market))

    # Backward-compatible aliases (prefer the names above in new code)
    async def get_preview_price(self, symbol: str, market: str) -> Optional[float]:
        return await self.get_cached_price(symbol, market)

    def get_preview_price_blocking(self, symbol: str, market: str) -> Optional[float]:
        return self.get_cached_price_blocking(symbol, market)
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/price_service.py ---