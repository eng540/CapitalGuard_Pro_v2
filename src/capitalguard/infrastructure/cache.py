# --- START OF FILE: src/capitalguard/infrastructure/cache.py ---
import time
from typing import Dict, Any, Optional, Tuple

class InMemoryCache:
    """
    A simple in-memory cache with Time-To-Live (TTL) support.
    This is used to temporarily store prices to avoid hitting API rate limits.
    """
    _cache: Dict[str, Tuple[Any, float]] = {}
    _ttl_seconds: int

    def __init__(self, ttl_seconds: int = 60):
        """
        Initializes the cache with a default Time-To-Live.
        :param ttl_seconds: How long an item should live in the cache before expiring.
        """
        self._ttl_seconds = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieves an item from the cache if it exists and has not expired.
        """
        if key not in self._cache:
            return None
        
        value, timestamp = self._cache[key]
        
        if time.time() - timestamp > self._ttl_seconds:
            # Item has expired, delete it and return None
            del self._cache[key]
            return None
            
        return value

    def set(self, key: str, value: Any) -> None:
        """
        Adds an item to the cache with the current timestamp.
        """
        self._cache[key] = (value, time.time())

# Create a global instance to be shared across the application
# This ensures that all parts of the app use the same cache instance.
price_cache = InMemoryCache(ttl_seconds=60)
# --- END OF FILE ---```

---

#### **ملف 2/6: خدمة السعر المحدثة (استبدال كامل)**
**المسار:** `src/capitalguard/application/services/price_service.py`
**الوصف:** إصدار جديد من خدمة السعر يستخدم آلية التخزين المؤقت.

```python
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