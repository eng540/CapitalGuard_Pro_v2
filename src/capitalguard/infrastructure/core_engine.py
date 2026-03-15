# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
# File: src/capitalguard/infrastructure/core_engine.py
# Version: v2.0-REDIS-FIX
#
# ✅ THE FIX (السبب D):
#   core_cache كان يُنشأ بدون redis_url → Redis لا يُستخدم أبداً
#   → أسعار الـ WebApp تُعرض "Loading..."
#   الإصلاح: core_cache يقرأ REDIS_URL من settings عند أول استخدام (lazy init).

import asyncio
import time
import logging
import json
import os
from typing import Any, Callable, Dict, Optional, TypeVar

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

T = TypeVar("T")
log = logging.getLogger(__name__)


class CacheStats:
    l1_hits: int = 0
    l2_hits: int = 0
    misses: int = 0


class AdvancedCacheSystem:
    """
    Hybrid Cache: L1 (memory) + L2 (Redis).
    ✅ FIX: redis_url يُقرأ من env عند أول استخدام إن لم يُمرَّر مباشرة.
    """

    def __init__(self, redis_url: str = None):
        self.l1_cache: Dict[str, Any] = {}
        self.l1_ttl: Dict[str, float] = {}
        self._redis_url = redis_url  # قد يكون None — يُحمَّل lazily
        self._redis: Optional[Any] = None
        self._redis_initialized = False
        self.stats = CacheStats()
        self._lock = asyncio.Lock() if self._is_in_loop() else None

    def _is_in_loop(self) -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _get_redis(self):
        """Lazy init لـ Redis — يقرأ REDIS_URL من env إن لم يُمرَّر."""
        if self._redis_initialized:
            return self._redis
        self._redis_initialized = True
        if not REDIS_AVAILABLE:
            return None
        url = self._redis_url or os.getenv("REDIS_URL")
        if not url:
            log.info("core_cache: No REDIS_URL — using memory-only cache.")
            return None
        try:
            self._redis = redis.from_url(url, decode_responses=False)
            log.info(f"core_cache: Redis connected at {url[:30]}...")
        except Exception as e:
            log.warning(f"core_cache: Redis init failed: {e}")
            self._redis = None
        return self._redis

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
                if data:
                    self.stats.l2_hits += 1
                    decoded = json.loads(data)
                    self.l1_cache[key] = decoded
                    self.l1_ttl[key] = time.time() + 10
                    return decoded
            except Exception as e:
                log.warning(f"Redis get error: {e}")

        self.stats.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int = 60):
        # L1
        self.l1_cache[key] = value
        self.l1_ttl[key] = time.time() + ttl
        # L2
        r = self._get_redis()
        if r:
            try:
                await r.setex(key, ttl, json.dumps(value, default=str))
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


# ✅ FIX: بدون redis_url — سيقرأ من REDIS_URL في .env عند أول استخدام
core_cache = AdvancedCacheSystem()
cb_telegram = CircuitBreaker("telegram_api", failure_threshold=3)
cb_db = CircuitBreaker("database", failure_threshold=5)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
