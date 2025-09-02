# --- START OF FILE: src/capitalguard/application/services/price_service.py ---
from __future__ import annotations
from dataclasses import dataclass
from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.cache import price_cache

@dataclass
class PriceService:
    """
    A service layer for fetching prices, with built-in caching to prevent rate limiting.
    """

    def get_cached_price(self, symbol: str, market: str) -> float | None:
        """
        Gets the price for a symbol, preferring a cached value if available and valid.
        If the price is not in the cache, it fetches from the provider and caches it.
        """
        cache_key = f"price:{market.lower()}:{symbol.upper()}"
        
        # 1. Try to get from cache first
        cached_price = price_cache.get(cache_key)
        if cached_price is not None:
            return cached_price
            
        # 2. If not in cache, fetch from the actual provider
        is_spot = (str(market or "Spot").lower().startswith("spot"))
        live_price = BinancePricing.get_price(symbol, spot=is_spot)
        
        # 3. If fetched successfully, store it in the cache for next time
        if live_price is not None:
            price_cache.set(cache_key, live_price)
            
        return live_price

    def get_preview_price(self, symbol: str, market: str) -> float | None:
        """This method now acts as an alias for the cached version for consistency."""
        return self.get_cached_price(symbol, market)
# --- END OF FILE ---