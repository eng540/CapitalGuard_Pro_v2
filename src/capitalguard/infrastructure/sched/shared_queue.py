# src/capitalguard/infrastructure/sched/shared_queue.py
"""
Thread-safe queue for communication between PriceStreamer and AlertService.
"""

import asyncio
import threading
from typing import Any, Tuple, Optional

class ThreadSafeQueue:
    """Queue آمنة للاستخدام بين الـ threads."""
    
    def __init__(self, maxsize: int = 0):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._put_event = asyncio.Event()
        
    async def put(self, item: Tuple[str, float, float]):
        """وضع عنصر في الـ queue."""
        with self._lock:
            await self._queue.put(item)
            self._put_event.set()  # إشعار بوجود بيانات جديدة
            
    async def get(self, timeout: Optional[float] = None):
        """أخذ عنصر من الـ queue مع timeout اختياري."""
        try:
            if timeout:
                # انتظار حتى تكون هناك بيانات أو انتهاء الوقت
                await asyncio.wait_for(self._put_event.wait(), timeout=timeout)
            else:
                await self._put_event.wait()
                
            with self._lock:
                if not self._queue.empty():
                    item = self._queue.get_nowait()
                    if self._queue.empty():
                        self._put_event.clear()  reset الإشعار إذا كانت الـ queue فارغة
                    return item
                else:
                    self._put_event.clear()
                    return None
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            return None
            
    def get_nowait(self):
        """محاولة أخذ عنصر بدون انتظار."""
        with self._lock:
            try:
                item = self._queue.get_nowait()
                if self._queue.empty():
                    self._put_event.clear()
                return item
            except asyncio.QueueEmpty:
                self._put_event.clear()
                return None
            
    def qsize(self):
        """حجم الـ queue."""
        with self._lock:
            return self._queue.qsize()
            
    def empty(self):
        """هل الـ queue فارغة؟"""
        with self._lock:
            return self._queue.empty()
            
    def task_done(self):
        """إعلام بانتهاء المهمة."""
        with self._lock:
            try:
                self._queue.task_done()
            except ValueError:
                pass  # تجاهل الخطأ إذا لم تكن هناك مهام منتظرة