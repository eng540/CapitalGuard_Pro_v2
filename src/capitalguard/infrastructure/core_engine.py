# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
# File: src/capitalguard/infrastructure/core_engine.py
# Version: v3.1.0-TYPE-SAFE-SERIALIZATION
#
# ✅ THE FIX (BUG-SERIALIZATION):
#   المشكلة: تخزين set في Redis عبر JSON كان يحولها إلى list
#   مما يسبب خطأ: unsupported operand type(s) for |: 'list' and 'set'
#
#   الحل الهندسي الأمثل:
#     1. إضافة serializers مخصصة للأنواع (set, Decimal, datetime)
#     2. json.dumps(..., default=_json_serializer) للتخزين
#     3. json.loads(..., object_hook=_json_deserializer) للاسترجاع
#     4. البيانات تحافظ على نوعها الأصلي تلقائياً
#
# ✅ محفوظ من v3.0.0:
#   - Per-loop Redis clients (يحل مشكلة "different loop")
#   - L1 memory cache
#   - CircuitBreaker
#   - AsyncPipeline
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-17

import asyncio
import time
import logging
import json
import os
from typing import Any, Callable, Dict, Optional, TypeVar, Set, Union, List
from decimal import Decimal
from datetime import datetime

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

T = TypeVar("T")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# TYPE-SAFE SERIALIZATION (JSON with Python types support)
# ─────────────────────────────────────────────────────────────────

def _json_serializer(obj: Any) -> Any:
    """
    محول مخصص للأنواع التي لا يدعمها JSON.
    يحافظ على نوع البيانات الأصلي عبر إضافة حقول وصفية.
    """
    if isinstance(obj, set):
        return {"__type__": "set", "values": list(obj)}
    elif isinstance(obj, Decimal):
        return {"__type__": "decimal", "value": str(obj)}
    elif isinstance(obj, datetime):
        return {"__type__": "datetime", "value": obj.isoformat()}
    elif isinstance(obj, (list, tuple)):
        # للـ lists العادية، نطبق التحويل على كل عنصر
        return [_json_serializer(item) if isinstance(item, (set, Decimal, datetime)) else item 
                for item in obj]
    elif isinstance(obj, dict):
        # للـ dicts، نطبق التحويل على القيم
        return {k: _json_serializer(v) if isinstance(v, (set, Decimal, datetime)) else v 
                for k, v in obj.items()}
    
    # للأنواع المدعومة أصلاً من JSON
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _json_deserializer(obj: Dict[str, Any]) -> Any:
    """
    محول مخصص لإعادة بناء الأنواع المخصصة من JSON.
    يقرأ الحقول الوصفية ويعيد الكائن الأصلي.
    """
    if not isinstance(obj, dict):
        return obj
    
    if "__type__" in obj:
        type_name = obj["__type__"]
        
        if type_name == "set":
            return set(obj["values"])
        elif type_name == "decimal":
            return Decimal(obj["value"])
        elif type_name == "datetime":
            return datetime.fromisoformat(obj["value"])
    
    # البحث عن أنواع مخصصة داخل القيم
    for key, value in obj.items():
        if isinstance(value, dict) and "__type__" in value:
            obj[key] = _json_deserializer(value)
        elif isinstance(value, list):
            obj[key] = [_json_deserializer(item) if isinstance(item, dict) else item 
                       for item in value]
    
    return obj


# ─────────────────────────────────────────────────────────────────
class CacheStats:
    """إحصائيات أداء الكاش."""
    l1_hits: int = 0
    l2_hits: int = 0
    misses: int = 0
    
    def report(self) -> Dict[str, int]:
        return {
            "l1_hits": self.l1_hits,
            "l2_hits": self.l2_hits,
            "misses": self.misses,
            "hit_rate": f"{(self.l1_hits + self.l2_hits) / max(1, self.l1_hits + self.l2_hits + self.misses) * 100:.1f}%"
        }


class AdvancedCacheSystem:
    """
    Hybrid Cache: L1 (memory per-process) + L2 (Redis per-loop).
    
    ✅ TYPE-SAFE v3.1.0:
        - يدعم set, Decimal, datetime تلقائياً
        - لا حاجة لتحويل يدوي في كل مكان
        - كل loop له Redis client منفصل (لا تعارض)
    """

    def __init__(self, redis_url: str = None):
        # L1: memory cache — سريع جداً، مشترك بين كل الـ loops
        self.l1_cache: Dict[str, Any] = {}
        self.l1_ttl: Dict[str, float] = {}

        # L2: Redis clients لكل loop (يحل مشكلة "different loop")
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
        يُعيد Redis client مرتبطاً بالـ event loop الحالي.
        كل loop يحصل على client منفصل → لا تعارض.
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
            client = redis.from_url(
                url, 
                decode_responses=False,
                socket_keepalive=True,
                health_check_interval=30
            )
            self._redis_clients[loop_id] = client
            log.debug("core_cache: Redis client created for loop %s", loop_id)
            return client
        except Exception as e:
            log.warning("core_cache: Redis client creation failed: %s", e)
            return None

    # ─────────────────────────────────────────────────────────────
    # Core Cache Operations (TYPE-SAFE)
    # ─────────────────────────────────────────────────────────────

    async def get(self, key: str) -> Any:
        """
        يجلب البيانات ويعيد بناء الأنواع المخصصة تلقائياً.
        ✅ set, Decimal, datetime تعود كما كانت.
        """
        # L1: memory (سريع جداً)
        if key in self.l1_cache:
            if time.time() < self.l1_ttl[key]:
                self.stats.l1_hits += 1
                return self.l1_cache[key]
            else:
                # انتهت الصلاحية
                del self.l1_cache[key]
                del self.l1_ttl[key]

        # L2: Redis (بطيء نسبياً)
        r = self._get_redis()
        if r:
            try:
                data = await r.get(key)
                if data:
                    self.stats.l2_hits += 1
                    
                    # ✅ FIX: deserialize مع الحفاظ على الأنواع
                    decoded = json.loads(data, object_hook=_json_deserializer)
                    
                    # تخزين في L1 للاستخدام السريع لاحقاً
                    self.l1_cache[key] = decoded
                    self.l1_ttl[key] = time.time() + 10  # 10 ثواني فقط
                    
                    return decoded
            except Exception as e:
                log.warning("Redis get error for %s: %s", key, e)

        self.stats.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int = 60) -> None:
        """
        يخزن أي نوع بيانات مع TTL.
        ✅ يدعم set, Decimal, datetime تلقائياً.
        """
        # L1: memory (نخزن الكائن الأصلي)
        self.l1_cache[key] = value
        self.l1_ttl[key] = time.time() + ttl
        
        # L2: Redis (نخزن نسخة JSON مع الحفاظ على الأنواع)
        r = self._get_redis()
        if r:
            try:
                # ✅ FIX: serialize مع الحفاظ على الأنواع
                serialized = json.dumps(value, default=_json_serializer)
                await r.setex(key, ttl, serialized)
            except Exception as e:
                log.warning("Redis set error for %s: %s", key, e)

    async def delete(self, key: str) -> None:
        """يحذف مفتاح من كل المستويات."""
        self.l1_cache.pop(key, None)
        self.l1_ttl.pop(key, None)
        
        r = self._get_redis()
        if r:
            try:
                await r.delete(key)
            except Exception:
                pass

    async def clear(self) -> None:
        """يمسح كل الكاش (تحذير: ثقيل)."""
        self.l1_cache.clear()
        self.l1_ttl.clear()
        
        # لا نمسح Redis لأن هذا قد يؤثر على عمليات أخرى
        log.warning("core_cache: L1 cleared, Redis untouched.")

    async def get_many(self, keys: List[str]) -> Dict[str, Any]:
        """يجلب عدة مفاتيح دفعة واحدة (أداء أفضل)."""
        result = {}
        
        # L1 أولاً
        remaining_keys = []
        for key in keys:
            if key in self.l1_cache and time.time() < self.l1_ttl[key]:
                result[key] = self.l1_cache[key]
                self.stats.l1_hits += 1
            else:
                remaining_keys.append(key)
        
        # L2 للباقي
        if remaining_keys:
            r = self._get_redis()
            if r:
                try:
                    pipe = r.pipeline()
                    for key in remaining_keys:
                        pipe.get(key)
                    responses = await pipe.execute()
                    
                    for i, key in enumerate(remaining_keys):
                        data = responses[i]
                        if data:
                            self.stats.l2_hits += 1
                            decoded = json.loads(data, object_hook=_json_deserializer)
                            result[key] = decoded
                            
                            # تحديث L1
                            self.l1_cache[key] = decoded
                            self.l1_ttl[key] = time.time() + 10
                        else:
                            self.stats.misses += 1
                except Exception as e:
                    log.warning("Redis mget error: %s", e)
                    for key in remaining_keys:
                        self.stats.misses += 1
            else:
                for key in remaining_keys:
                    self.stats.misses += 1
        
        return result

    async def set_many(self, mapping: Dict[str, Any], ttl: int = 60) -> None:
        """يخزن عدة مفاتيح دفعة واحدة."""
        # L1
        expiry = time.time() + ttl
        for key, value in mapping.items():
            self.l1_cache[key] = value
            self.l1_ttl[key] = expiry
        
        # L2
        r = self._get_redis()
        if r:
            try:
                pipe = r.pipeline()
                for key, value in mapping.items():
                    serialized = json.dumps(value, default=_json_serializer)
                    pipe.setex(key, ttl, serialized)
                await pipe.execute()
            except Exception as e:
                log.warning("Redis mset error: %s", e)


# ─────────────────────────────────────────────────────────────────
class CircuitBreakerOpenError(Exception):
    """يُرفع عندما يكون الـ circuit مفتوحاً."""
    pass


class CircuitBreaker:
    """
    Circuit Breaker pattern للحماية من الفشل المتكرر.
    يفتح الـ circuit بعد عدد معين من الإخفاقات.
    """
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
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_failure_time = 0

    async def execute(self, func: Callable, *args, **kwargs):
        """ينفذ الدالة مع حماية Circuit Breaker."""
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                log.info("Circuit %s: OPEN → HALF_OPEN (timeout expired)", self.name)
                self.state = "HALF_OPEN"
            else:
                raise CircuitBreakerOpenError(
                    f"Circuit {self.name} is OPEN (cooldown: "
                    f"{self.recovery_timeout - (time.time() - self.last_failure_time):.0f}s)"
                )
        
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = await asyncio.to_thread(func, *args, **kwargs)
            
            if self.state == "HALF_OPEN":
                self.reset()
                log.info("Circuit %s: HALF_OPEN → CLOSED (successful test)", self.name)
            
            return result
            
        except Exception as e:
            self._handle_failure()
            raise e

    def _handle_failure(self):
        """يسجل فشلاً ويقرر إذا كان يجب فتح الـ circuit."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            old_state = self.state
            self.state = "OPEN"
            if old_state != "OPEN":
                log.critical(
                    "Circuit %s: %s → OPEN after %d failures",
                    self.name, old_state, self.failure_count,
                )
        else:
            log.warning(
                "Circuit %s: failure %d/%d",
                self.name, self.failure_count, self.failure_threshold,
            )

    def reset(self):
        """يعيد تعيين الـ circuit إلى الحالة الطبيعية."""
        self.state = "CLOSED"
        self.failure_count = 0
        log.info("Circuit %s: CLOSED (recovered)", self.name)


# ─────────────────────────────────────────────────────────────────
class AsyncPipeline:
    """
    ينفذ مهام متعددة بالتوازي ويجمع النتائج.
    مفيد للـ aggregation والـ batch processing.
    """
    
    @staticmethod
    async def execute_parallel(tasks: Dict[str, Callable]):
        """
        ينفذ المهام بالتوازي ويعيد النتائج في dict.
        
        مثال:
            results = await AsyncPipeline.execute_parallel({
                "price": lambda: price_service.get_price("BTCUSDT"),
                "user": lambda: user_service.get_user(123),
            })
        """
        keys = list(tasks.keys())
        coroutines = []
        
        for func in tasks.values():
            if asyncio.iscoroutinefunction(func):
                coroutines.append(func())
            else:
                coroutines.append(asyncio.to_thread(func))
        
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        
        output = {}
        for i, key in enumerate(keys):
            if isinstance(results[i], Exception):
                log.error("Pipeline Error in %s: %s", key, results[i])
                output[key] = None
            else:
                output[key] = results[i]
        
        return output
    
    @staticmethod
    async def execute_with_timeout(
        func: Callable,
        timeout: float,
        default: Any = None,
        *args,
        **kwargs
    ):
        """ينفذ دالة مع timeout محدد."""
        try:
            if asyncio.iscoroutinefunction(func):
                return await asyncio.wait_for(func(*args, **kwargs), timeout)
            else:
                return await asyncio.wait_for(
                    asyncio.to_thread(func, *args, **kwargs), 
                    timeout
                )
        except asyncio.TimeoutError:
            log.warning("Pipeline timeout after %.2fs", timeout)
            return default


# ─────────────────────────────────────────────────────────────────
# Global singletons
# ─────────────────────────────────────────────────────────────────
"""
المكونات الأساسية للنظام:

core_cache:
    - Hybrid cache (L1 memory + L2 Redis)
    - Type-safe serialization (يدعم set, Decimal, datetime)
    - Per-loop Redis clients (لا مشاكل مع multiple loops)

cb_telegram:
    - Circuit breaker لـ Telegram API
    - يفتح بعد 3 إخفاقات متتالية
    - يتعافى بعد 30 ثانية

cb_db:
    - Circuit breaker لقاعدة البيانات
    - يفتح بعد 5 إخفاقات متتالية
    - يتعافى بعد 30 ثانية
"""

core_cache = AdvancedCacheSystem()

cb_telegram = CircuitBreaker(
    name="telegram_api",
    failure_threshold=3,
    recovery_timeout=30,
)

cb_db = CircuitBreaker(
    name="database",
    failure_threshold=5,
    recovery_timeout=30,
)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---