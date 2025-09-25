# src/capitalguard/infrastructure/sched/shared_queue.py
"""
Thread-safe queue for communication between PriceStreamer and AlertService.
"""

import asyncio
import threading
from typing import Any, Tuple, Optional

class ThreadSafeQueue:
    """Thread-safe queue for inter-thread communication."""
    
    def __init__(self, maxsize: int = 0):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._put_event = asyncio.Event()
        
    async def put(self, item: Tuple[str, float, float]):
        """Put an item into the queue."""
        with self._lock:
            await self._queue.put(item)
            self._put_event.set()  # Notify that there's new data
            
    async def get(self, timeout: Optional[float] = None):
        """Get an item from the queue with optional timeout."""
        try:
            if timeout:
                # Wait for data or timeout
                await asyncio.wait_for(self._put_event.wait(), timeout=timeout)
            else:
                await self._put_event.wait()
                
            with self._lock:
                if not self._queue.empty():
                    item = self._queue.get_nowait()
                    if self._queue.empty():
                        self._put_event.clear()  # Reset event if queue is empty
                    return item
                else:
                    self._put_event.clear()
                    return None
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            return None
            
    def get_nowait(self):
        """Try to get an item without waiting."""
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
        """Get the queue size."""
        with self._lock:
            return self._queue.qsize()
            
    def empty(self):
        """Check if the queue is empty."""
        with self._lock:
            return self._queue.empty()
            
    def task_done(self):
        """Mark task as done."""
        with self._lock:
            try:
                self._queue.task_done()
            except ValueError:
                pass  # Ignore error if no tasks are waiting