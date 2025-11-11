# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/price_service.py ---
# src/capitalguard/application/services/price_service.py (v16.3.2 - Symbol Hotfix)
"""
Price fetching service with a small cache and pluggable providers.
✅ THE FIX (v16.3.2): Added resiliency to `get_cached_price`.
    - It now automatically appends "USDT" to symbols that
      appear to be base assets (e.g., "SOL", "LINK"), fixing
      the "Invalid symbol" errors seen in logs.
"""
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

    def _normalize_symbol(self, symbol: str) -> str:
        """
        Ensures the symbol is a valid trading pair.
        ✅ THE FIX: Appends 'USDT' if the symbol looks like a base asset.
        """
        symbol_upper = (symbol or "").strip().upper()

        # If it already contains common pair identifiers, assume it's a valid pair
        if any(pair in symbol_upper for pair in ["USDT", "PERP", "BTC", "ETH", "BUSD", "USDC"]):
            return symbol_upper

        # If it's short (like "SOL", "LINK") and doesn't look like a pair, append USDT
        if 2 <= len(symbol_upper) <= 5 and symbol_upper.isalpha():
            normalized = f"{symbol_upper}USDT"
            log.debug("Normalizing symbol '%s' to '%s'", symbol, normalized)
            return normalized

        return symbol_upper

    async def get_cached_price(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        """
        Async: Return cached price if available; otherwise fetch from provider and cache it.

        Args:
            symbol (str): The trading symbol (e.g., "BTCUSDT" or "BTC").
            market (str): The market type (e.g., "Futures" or "Spot").
            force_refresh (bool): If True, bypasses the cache and fetches a fresh price.

        Returns:
            Optional[float]: The latest price or None if unavailable.
        """
        if not symbol:
            return None

        # Normalize symbol before use
        normalized_symbol = self._normalize_symbol(symbol)

        provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        cache_key = f"price:{provider}:{(market or 'spot').lower()}:{normalized_symbol}"

        if not force_refresh:
            try:
                cached_price = price_cache.get(cache_key)
            except Exception:
                cached_price = None
            if cached_price is not None:
                return cached_price

        live_price: Optional[float] = None

        try:
            if provider == "binance":
                is_spot = str(market or "Spot").lower().startswith("spot")
                loop = asyncio.get_running_loop()
                # Run blocking provider call in executor
                live_price = await loop.run_in_executor(None, BinancePricing.get_price, normalized_symbol, is_spot)

            elif provider == "coingecko":
                cg_client = CoinGeckoClient()
                # CoinGecko client expected to be async
                live_price = await cg_client.get_price(normalized_symbol)

            else:
                log.error("Unknown market data provider: %s", provider)
                return None

        except Exception as e:
            # Log full context for debugging but do not raise
            log.error("Price fetch failed for %s (provider=%s, market=%s): %s", normalized_symbol, provider, market, exc_info=e)
            live_price = None

        if live_price is not None:
            ttl = 30 if provider == "coingecko" else 60
            try:
                price_cache.set(cache_key, live_price, ttl_seconds=ttl)
            except Exception:
                # Cache failure should not break caller
                log.debug("Failed to write price to cache for %s", cache_key, exc_info=False)

        return live_price

    # Backward-compatible alias (async)
    async def get_preview_price(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        return await self.get_cached_price(symbol, market, force_refresh)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/price_service.py ---