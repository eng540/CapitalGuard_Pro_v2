#START src/capitalguard/infrastructure/cache.py
# --- START OF FULL, RE-ARCHITECTED, AND FINAL FILE ---
import time
from typing import Dict, Any, Optional, Tuple

class InMemoryCache:
    """
    A simple in-memory cache with item-specific Time-To-Live (TTL) support.
    """
    # The cache now stores: { key: (value, expiry_timestamp) }
    _cache: Dict[str, Tuple[Any, float]] = {}
    _default_ttl_seconds: int

    def __init__(self, ttl_seconds: int = 60):
        """
        Initializes the cache with a default Time-To-Live.
        :param ttl_seconds: The default lifespan for an item if not specified otherwise.
        """
        self._default_ttl_seconds = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieves an item from the cache if it exists and has not expired.
        """
        if key not in self._cache:
            return None
        
        value, expiry_timestamp = self._cache[key]
        
        if time.time() > expiry_timestamp:
            # Item has expired, delete it and return None
            del self._cache[key]
            return None
            
        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """
        Adds an item to the cache with a specific or default TTL.
        :param key: The key for the cache item.
        :param value: The value to be stored.
        :param ttl_seconds: Optional. The specific TTL for this item in seconds.
                            If None, the default TTL is used.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        expiry_timestamp = time.time() + ttl
        self._cache[key] = (value, expiry_timestamp)

# Create a global instance to be shared across the application
price_cache = InMemoryCache(ttl_seconds=60)
# --- END OF FULL, RE-ARCHITECTED, AND FINAL FILE ---
#END