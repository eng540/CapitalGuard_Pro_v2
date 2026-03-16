#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/core_engine.py ---
import os
import time
import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional, TypeVar

from capitalguard.config import settings
from capitalguard.infrastructure.cache import RedisCache

T = TypeVar("T")
log = logging.getLogger(__name__)

# -------------------------------
# إعداد RedisCache الجديد (lazy + loop safe)
# -------------------------------
REDIS_URL = getattr(settings, "REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
_global_redis_cache = RedisCache(url=REDIS_URL)

# -------------------------------
# CacheStats
# -------------------------------
class CacheStats:
    l1_hits: int = 0
    l2_hits: int = 0
    misses: int = 0

# -------------------------------
# AdvancedCacheSystem مع دعم RedisCache الجديد
# -------------------------------
class AdvancedCacheSystem:
    """
    Hybrid Cache: L1 (memory) + L2 (Redis via RedisCache)
    """
    def __init__(self):
        self.l1_cache: Dict[str, Any] = {}
        self.l1_ttl: Dict[str, float] = {}
        self.stats = CacheStats()
        self._lock = asyncio.Lock() if self._is_in_loop() else None

    def _is_in_loop(self) -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _get_redis(self) -> Optional[RedisCache]:
        """استخدام RedisCache الجديد"""
        return _global_redis_cache

    async def get(self, key: str) -> Any:
        # L1
        if key in self.l1_cache:
            if time.time() < self.l1_ttl[key]:
                self.stats.l1_hits += 1
                return self.l1_cache[key]
            else:
                del self.l1_cache[key]

        # L2 Redis
        r = self._get_redis()
        if r:
            try:
                data = await r.get(key)
                if data is not None:
                    self.stats.l2_hits += 1
                    self.l1_cache[key] = data
                    self.l1_ttl[key] = time.time() + 10
                    return data
            except Exception as e:
                log.warning(f"Redis get error: {e}")

        self.stats.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int = 60):
        # L1
        self.l1_cache[key] = value
        self.l1_ttl[key] = time.time() + ttl
        # L2 Redis
        r = self._get_redis()
        if r:
            try:
                await r.set(key, value, ttl)
            except Exception as e:
                log.warning(f"Redis set error: {e}")

    async def delete(self, key: str):
        self.l1_cache.pop(key, None)
        self.l1_ttl.pop(key, None)
        r = self._get_redis()
        if r:
            try:
                await r.delete(key)
            except Exception:
                pass

# -------------------------------
# Circuit Breaker
# -------------------------------
class CircuitBreakerOpenError(Exception):
    pass

class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: int = 30):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = "CLOSED"
        self.last_failure_time = 0

    async def execute(self, func: Callable, *args, **kwargs):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
            else:
                raise CircuitBreakerOpenError(f"Circuit {self.name} is OPEN.")
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = await asyncio.to_thread(func, *args, **kwargs)
            if self.state == "HALF_OPEN":
                self.reset()
            return result
        except Exception as e:
            self._handle_failure()
            raise e

    def _handle_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            log.critical(f"Circuit {self.name} OPENED after {self.failure_count} failures.")

    def reset(self):
        self.state = "CLOSED"
        self.failure_count = 0
        log.info(f"Circuit {self.name} CLOSED (Recovered).")

# -------------------------------
# AsyncPipeline
# -------------------------------
class AsyncPipeline:
    @staticmethod
    async def execute_parallel(tasks: Dict[str, Callable]):
        keys = list(tasks.keys())
        coroutines = [
            func() if asyncio.iscoroutinefunction(func) else asyncio.to_thread(func)
            for func in tasks.values()
        ]
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        output = {}
        for i, key in enumerate(keys):
            if isinstance(results[i], Exception):
                log.error(f"Pipeline Error in {key}: {results[i]}")
                output[key] = None
            else:
                output[key] = results[i]
        return output

# -------------------------------
# Core cache and circuit breakers instances
# -------------------------------
core_cache = AdvancedCacheSystem()
cb_telegram = CircuitBreaker("telegram_api", failure_threshold=3)
cb_db = CircuitBreaker("database", failure_threshold=5)

log.info("Core Engine components initialized with L1+RedisCache hybrid.")
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/core_engine.py ---