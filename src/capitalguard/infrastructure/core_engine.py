# --- START OF NEW FILE: src/capitalguard/infrastructure/core_engine.py --- v1
# Architecture: Resilience & Performance Layer
# Components: L1/L2 Cache, Circuit Breaker, Async Pipeline
# ✅ Implements "The Vision" Infrastructure

import asyncio
import time
import logging
import json
from typing import Any, Callable, Dict, Optional, TypeVar, Generic
from dataclasses import dataclass, field

# افتراض وجود Redis (يمكن استبداله بـ Mock إذا لم يكن متوفراً)
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

T = TypeVar("T")
log = logging.getLogger(__name__)

# 1. ✅ نظام التخزين المؤقت متعدد المستويات (L1/L2 Cache)
class CacheStats:
    l1_hits: int = 0
    l2_hits: int = 0
    misses: int = 0

class AdvancedCacheSystem:
    """
    Hybrid Caching System:
    L1: Memory (Fastest, Process-bound)
    L2: Redis (Distributed, Persistent)
    """
    def __init__(self, redis_url: str = None):
        self.l1_cache: Dict[str, Any] = {}
        self.l1_ttl: Dict[str, float] = {}
        # NOTE: Pass the actual Redis URL from environment variables here
        self.redis = redis.from_url(redis_url) if REDIS_AVAILABLE and redis_url else None
        self.stats = CacheStats()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        # 1. Check L1 (Memory)
        if key in self.l1_cache:
            if time.time() < self.l1_ttl[key]:
                self.stats.l1_hits += 1
                return self.l1_cache[key]
            else:
                del self.l1_cache[key] # Expired

        # 2. Check L2 (Redis)
        if self.redis:
            try:
                data = await self.redis.get(key)
                if data:
                    self.stats.l2_hits += 1
                    decoded = json.loads(data)
                    # Populate L1 for next time (Hot Path)
                    await self._set_l1(key, decoded, ttl=10) 
                    return decoded
            except Exception as e:
                log.warning(f"Redis L2 Error: {e}")

        self.stats.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int = 60):
        # Set L1
        await self._set_l1(key, value, ttl)
        # Set L2
        if self.redis:
            try:
                serialized = json.dumps(value, default=str)
                await self.redis.setex(key, ttl, serialized)
            except Exception as e:
                log.warning(f"Redis Set Error: {e}")

    async def _set_l1(self, key: str, value: Any, ttl: int):
        async with self._lock:
            self.l1_cache[key] = value
            self.l1_ttl[key] = time.time() + ttl

# 2. ✅ قاطع الدائرة المالية (Financial Circuit Breaker)
class CircuitBreakerOpenError(Exception):
    pass

class CircuitBreaker:
    """
    Protects the system from cascading failures (e.g., Telegram/Binance API down).
    States: CLOSED (Normal) -> OPEN (Failing) -> HALF_OPEN (Testing)
    """
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
                log.info(f"Circuit {self.name} HALF_OPEN: Testing service recovery.")
            else:
                raise CircuitBreakerOpenError(f"Circuit {self.name} is OPEN. Service unavailable.")

        try:
            # Use asyncio.to_thread if the function is not async (e.g. DB access)
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
            log.critical(f"🚨 Circuit {self.name} OPENED after {self.failure_count} failures.")

    def reset(self):
        self.state = "CLOSED"
        self.failure_count = 0
        log.info(f"Circuit {self.name} CLOSED (Recovered).")

# 3. ✅ خط المعالجة غير المتزامن (Async Pipeline)
class AsyncPipeline:
    """
    Executes independent tasks in parallel for maximum throughput.
    """
    @staticmethod
    async def execute_parallel(tasks: Dict[str, Callable]):
        """
        Input: {'user_data': get_user_callable, 'market_data': get_prices_callable}
        Output: {'user_data': Result1, 'market_data': Result2}
        """
        keys = list(tasks.keys())
        # Ensure all callables are awaited if they are coroutines, or run in thread pool
        coroutines = []
        for func in tasks.values():
            if asyncio.iscoroutinefunction(func):
                coroutines.append(func())
            else:
                coroutines.append(asyncio.to_thread(func))
        
        # Run all concurrently
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        
        output = {}
        for i, key in enumerate(keys):
            if isinstance(results[i], Exception):
                log.error(f"Pipeline Error in {key}: {results[i]}")
                output[key] = None
            else:
                output[key] = results[i]
        return output

# --- Global Instances (Singleton Pattern) ---
# NOTE: In a real app, pass the Redis URL from config (e.g. os.getenv('REDIS_URL'))
core_cache = AdvancedCacheSystem() 
cb_telegram = CircuitBreaker("telegram_api", failure_threshold=3)
cb_db = CircuitBreaker("database", failure_threshold=5)

# --- END OF NEW FILE: src/capitalguard/infrastructure/core_engine.py ---