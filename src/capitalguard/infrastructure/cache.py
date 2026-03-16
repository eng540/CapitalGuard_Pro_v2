#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/cache.py ---
import json
import logging
from typing import Any, Optional
import redis.asyncio as redis

log = logging.getLogger(__name__)

class RedisCache:
    def __init__(self, url: str):
        self.url = url
        self._redis: Optional[redis.Redis] = None

    @property
    def client(self) -> redis.Redis:
        """
        🔥 Lazy Initialization: 
        يتم بناء الاتصال فقط عند الحاجة إليه لضمان ارتباطه بالغرفة الصحيحة (Current Event Loop).
        """
        if self._redis is None:
            self._redis = redis.from_url(
                self.url, 
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            log.info("✅ Redis connection lazily initialized & bound to the active event loop.")
        return self._redis

    async def get(self, key: str) -> Optional[Any]:
        try:
            val = await self.client.get(key)
            if val:
                return json.loads(val)
            return None
        except Exception as e:
            log.error(f"Redis GET Error for key {key}: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 60) -> bool:
        try:
            val = json.dumps(value)
            await self.client.set(key, val, ex=ttl)
            return True
        except Exception as e:
            log.error(f"Redis SET Error for key {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        try:
            await self.client.delete(key)
            return True
        except Exception as e:
            log.error(f"Redis DELETE Error for key {key}: {e}")
            return False

    async def close(self):
        if self._redis:
            await self._redis.aclose()
            log.info("Redis connection closed securely.")
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/cache.py ---