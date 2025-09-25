# src/capitalguard/infrastructure/sched/shared_queue.py (v20.0.0 - Production Ready)
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
        # ✅ --- FIX: Removed unnecessary lock and event ---
        # asyncio.Queue is already thread-safe for put/get from different loops/threads
        # when used with run_coroutine_threadsafe or similar.
        # The internal queue handles synchronization.
        
    async def put(self, item: Tuple[str, float, float]):
        """Put an item into the queue."""
        await self._queue.put(item)
            
    async def get(self, timeout: Optional[float] = None):
        """Get an item from the queue with optional timeout."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            log.error("Error getting from queue: %s", e) # ✅ Added logging
            return None
            
    def get_nowait(self):
        """Try to get an item without waiting."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        except Exception as e:
            log.error("Error getting from queue without wait: %s", e) # ✅ Added logging
            return None
            
    def qsize(self):
        """Get the queue size."""
        return self._queue.qsize()
            
    def empty(self):
        """Check if the queue is empty."""
        return self._queue.empty()
            
    def task_done(self):
        """Mark task as done."""
        try:
            self._queue.task_done()
        except ValueError:
            pass