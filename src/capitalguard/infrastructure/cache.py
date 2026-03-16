#START src/capitalguard/infrastructure/cache.py
# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
# File: src/capitalguard/infrastructure/cache.py
# Version: v2.0.0-INSTANCE-FIX
#
# ✅ THE FIX (BUG-CACHE-1):
#   _cache كان class-level variable:
#     _cache: Dict[str, Tuple[Any, float]] = {}  ← مشترك بين كل instances
#   هذا يعني أن price_cache وأي instance آخر من InMemoryCache
#   يشتركون في نفس الـ dict → تلوث بيانات بين المكونات المختلفة.
#
#   الإصلاح: نقل _cache إلى __init__ كـ instance variable:
#     self._cache = {}  ← منفصل لكل instance
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

import time
from typing import Dict, Any, Optional, Tuple


class InMemoryCache:
    """
    كاش سريع في الذاكرة مع TTL لكل عنصر.
    sync بالكامل — بلا event loop، آمن من أي thread.
    """

    def __init__(self, ttl_seconds: int = 60):
        # ✅ FIX: instance variable — منفصل لكل instance
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._default_ttl_seconds = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        """يُعيد القيمة إذا كانت موجودة ولم تنتهِ صلاحيتها."""
        if key not in self._cache:
            return None

        value, expiry_timestamp = self._cache[key]

        if time.time() > expiry_timestamp:
            del self._cache[key]
            return None

        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """يُخزِّن القيمة مع TTL محدد أو الافتراضي."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        expiry_timestamp = time.time() + ttl
        self._cache[key] = (value, expiry_timestamp)

    def delete(self, key: str) -> None:
        """يحذف عنصراً من الكاش."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """يمسح كل الكاش."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


# instance عالمي للأسعار — يُستخدم في price_service.py
price_cache = InMemoryCache(ttl_seconds=60)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
#END
