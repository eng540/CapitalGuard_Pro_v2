# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
# File: src/capitalguard/infrastructure/core_engine.py
# Version: v3.0.0-PER-LOOP-REDIS
#
# ✅ THE FIX (BUG-REDIS-LOOP):
#   "got Future <Future pending> attached to a different loop"
#
#   السبب:
#     redis.asyncio يُنشئ connection pool مرتبطاً بالـ event loop
#     الذي كان نشطاً عند إنشائه.
#     النظام يملك loop-ين:
#       Loop A: AlertService bg thread (يُنشئ Redis connection)
#       Loop B: FastAPI/uvicorn (يحاول استخدام نفس connection)
#     → خطأ "attached to a different loop"
#
#   الإصلاح:
#     بدلاً من connection واحد مشترك، نحتفظ بـ dict:
#       { loop_id → redis_client }
#     كل event loop يحصل على client منفصل خاص به.
#     _get_redis() يقرأ الـ loop الحالي ويُعيد client مناسب.
#
# ✅ محفوظ من v2.0:
#   - lazy REDIS_URL من env
#   - L1 memory cache
#   - CircuitBreaker
#   - AsyncPipeline
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

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


# ─────────────────────────────────────────────────────────────────
class CacheStats:
    l1_hits: int = 0
    l2_hits: int = 0
    misses: int = 0


class AdvancedCacheSystem:
    """
    Hybrid Cache: L1 (memory per-process) + L2 (Redis per-loop).

    ✅ FIX v3.0: كل event loop يحصل على Redis client منفصل.
    يحل مشكلة "attached to a different loop" الناتجة عن وجود
    AlertService bg thread وFastAPI loop في نفس الوقت.
    """

    def __init__(self, redis_url: str = None):
        # L1: memory cache — مشترك بين كل الـ loops (بيانات غير async)
        self.l1_cache: Dict[str, Any] = {}
        self.l1_ttl: Dict[str, float] = {}

        # ✅ FIX v3.0: dict من loop_id → redis_client
        self._redis_url = redis_url
        self._redis_clients: Dict[int, Any] = {}   # { id(loop) → client }
        self._redis_url_resolved: Optional[str] = None
        self._url_resolved = False

        self.stats = CacheStats()

    def _resolve_url(self) -> Optional[str]:
        """يقرأ REDIS_URL مرة واحدة ويُخزِّنه."""
        if not self._url_resolved:
            self._redis_url_resolved = self._redis_url or os.getenv("REDIS_URL")
            self._url_resolved = True
            if not self._redis_url_resolved:
                log.info("core_cache: No REDIS_URL — memory-only cache.")
        return self._redis_url_resolved

    def _get_redis(self) -> Optional[Any]:
        """
        ✅ FIX v3.0: يُعيد Redis client مرتبطاً بالـ event loop الحالي.
        كل loop يحصل على client منفصل → لا تعارض بين loops.
        """
        if not REDIS_AVAILABLE:
            return None

        url = self._resolve_url()
        if not url:
            return None

        # تحديد الـ loop الحالي
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
        except RuntimeError:
            # لا يوجد loop نشط — لا يمكن استخدام Redis async
            return None

        # إذا كان لهذا الـ loop client موجود → أعِده
        if loop_id in self._redis_clients:
            return self._redis_clients[loop_id]

        # إنشاء client جديد لهذا الـ loop
        try:
            client = redis.from_url(url, decode_responses=False)
            self._redis_clients[loop_id] = client
            log.info(
                "core_cache: Redis client created for loop %s (url=%s...)",
                loop_id, url[:30],
            )
            return client
        except Exception as e:
            log.warning("core_cache: Redis client creation failed: %s", e)
            return None

    # ─────────────────────────────────────────────────────────────
    # Cache operations
    # ─────────────────────────────────────────────────────────────

    async def get(self, key: str) -> Any:
        # L1: memory
        if key in self.l1_cache:
            if time.time() < self.l1_ttl[key]:
                self.stats.l1_hits += 1
                return self.l1_cache[key]
            else:
                del self.l1_cache[key]

        # L2: Redis (loop-safe)
        r = self._get_redis()
        if r:
            try:
                data = await r.get(key)
                if data:
                    self.stats.l2_hits += 1
                    decoded = json.loads(data)
                    # populate L1
                    self.l1_cache[key] = decoded
                    self.l1_ttl[key] = time.time() + 10
                    return decoded
            except Exception as e:
                log.warning("Redis get error: %s", e)

        self.stats.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int = 60) -> None:
        # L1
        self.l1_cache[key] = value
        self.l1_ttl[key] = time.time() + ttl
        # L2
        r = self._get_redis()
        if r:
            try:
                await r.setex(key, ttl, json.dumps(value, default=str))
            except Exception as e:
                log.warning("Redis set error: %s", e)

    async def delete(self, key: str) -> None:
        self.l1_cache.pop(key, None)
        self.l1_ttl.pop(key, None)
        r = self._get_redis()
        if r:
            try:
                await r.delete(key)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────
class CircuitBreakerOpenError(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
    ):
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
                raise CircuitBreakerOpenError(
                    f"Circuit {self.name} is OPEN."
                )
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
            log.critical(
                "Circuit %s OPENED after %d failures.",
                self.name, self.failure_count,
            )

    def reset(self):
        self.state = "CLOSED"
        self.failure_count = 0
        log.info("Circuit %s CLOSED (Recovered).", self.name)


# ─────────────────────────────────────────────────────────────────
class AsyncPipeline:
    @staticmethod
    async def execute_parallel(tasks: Dict[str, Callable]):
        keys = list(tasks.keys())
        coroutines = [
            func() if asyncio.iscoroutinefunction(func)
            else asyncio.to_thread(func)
            for func in tasks.values()
        ]
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        output = {}
        for i, key in enumerate(keys):
            if isinstance(results[i], Exception):
                log.error("Pipeline Error in %s: %s", key, results[i])
                output[key] = None
            else:
                output[key] = results[i]
        return output


# ─────────────────────────────────────────────────────────────────
# Global singletons
# ─────────────────────────────────────────────────────────────────
# REDIS_URL يُقرأ من env تلقائياً عند أول استخدام.
# كل event loop يحصل على Redis client منفصل.
core_cache  = AdvancedCacheSystem()
cb_telegram = CircuitBreaker("telegram_api", failure_threshold=3)
cb_db       = CircuitBreaker("database",     failure_threshold=5)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
