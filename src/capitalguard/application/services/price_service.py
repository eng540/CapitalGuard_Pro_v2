#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/price_service.py ---
# src/capitalguard/application/services/price_service.py (v16.3.4 - Auto-Failover Fix)
"""
Price fetching service with a small cache and pluggable providers.
✅ THE FIX (v16.3.4): Implemented automatic failover to CoinGecko.
    - If Binance returns None (due to Geo-Block or timeout), the service
      IMMEDIATELY tries CoinGecko within the same request.
    - This ensures the user doesn't get "Unable to fetch price" errors.
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
        """
        if not symbol:
            return None

        # Normalize symbol before use
        normalized_symbol = self._normalize_symbol(symbol)

        # Check cache first
        provider_env = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        cache_key = f"price:any:{(market or 'spot').lower()}:{normalized_symbol}"

        if not force_refresh:
            try:
                cached_price = price_cache.get(cache_key)
                if cached_price is not None:
                    return cached_price
            except Exception:
                pass

        live_price: Optional[float] = None
        loop = asyncio.get_running_loop()

        # --- Strategy: Try Binance First, Fallback to CoinGecko ---
        
        # 1. Try Binance (if configured as primary or default)
        if provider_env == "binance":
            try:
                is_spot = str(market or "Spot").lower().startswith("spot")
                # Run blocking provider call in executor
                live_price = await loop.run_in_executor(None, BinancePricing.get_price, normalized_symbol, is_spot)
            except Exception as e:
                log.warning(f"Binance price fetch failed for {normalized_symbol}: {e}")
                live_price = None

        # 2. Auto-Failover: If Binance failed (or provider is coingecko), try CoinGecko
        if live_price is None:
            if provider_env == "binance":
                log.info(f"⚠️ Binance failed/blocked for {normalized_symbol}. Failing over to CoinGecko...")
            
            try:
                cg_client = CoinGeckoClient()
                live_price = await cg_client.get_price(normalized_symbol)
                if live_price:
                    log.info(f"✅ CoinGecko successfully retrieved price for {normalized_symbol}: {live_price}")
            except Exception as e:
                log.error(f"CoinGecko fallback failed for {normalized_symbol}: {e}")
                live_price = None

        # 3. Cache and Return
        if live_price is not None:
            # Cache for 60 seconds to reduce API load
            try:
                price_cache.set(cache_key, live_price, ttl_seconds=60)
            except Exception:
                pass
            return live_price
        else:
            log.error(f"❌ All providers failed to fetch price for {normalized_symbol}")
            return None

    # Backward-compatible alias (async)
    async def get_preview_price(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        return await self.get_cached_price(symbol, market, force_refresh)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/price_service.py ---