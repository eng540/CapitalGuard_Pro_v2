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
# --- END OF FILE ---``