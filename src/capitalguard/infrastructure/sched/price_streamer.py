# src/capitalguard/infrastructure/sched/price_streamer.py (v19.0.8 - Fixed)
"""
PriceStreamer - إصلاح موثوقية اتصال WebSocket ومعالجة البيانات
"""

import asyncio
import logging
import time
from typing import List, Set

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger("capitalguard.streamer")

class PriceStreamer:
    def __init__(self, queue: asyncio.Queue, repo: RecommendationRepository):
        self._queue = queue
        self._repo = repo
        self._ws_client = BinanceWS()
        self._task: asyncio.Task = None
        self._is_running = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._last_symbols_count = 0

    async def _price_handler(self, symbol: str, low_price: float, high_price: float):
        """معالج البيانات مع تحسينات المرونة"""
        try:
            # ✅ سجل وصول البيانات
            if self._reconnect_attempts > 0:
                log.info("✅ WebSocket reconnected successfully, processing data...")
                self._reconnect_attempts = 0
                
            # ✅ أضف timestamp للبيانات
            timestamp = time.time()
            
            # ✅ حاول إرسال البيانات إلى الطابور مع timeout
            try:
                await asyncio.wait_for(
                    self._queue.put((symbol, low_price, high_price, timestamp)), 
                    timeout=5.0
                )
                log.debug("📤 Sent to queue: %s L:%.6f H:%.6f", symbol, low_price, high_price)
            except asyncio.TimeoutError:
                log.warning("⏰ Queue put timeout for %s - queue might be full", symbol)
                
        except Exception as e:
            log.error("❌ Error in price handler for %s: %s", symbol, e)

    def _get_symbols_to_watch(self) -> List[str]:
        """الحصول على الرموز للمتابعة مع تحسينات"""
        try:
            with SessionLocal() as session:
                open_recs_orm = self._repo.list_open_orm(session)
                symbols = list({rec.asset for rec in open_recs_orm})
                
                current_count = len(symbols)
                if current_count != self._last_symbols_count:
                    log.info("🔍 Watching %d symbols: %s", current_count, symbols)
                    self._last_symbols_count = current_count
                    
                return symbols
                
        except Exception as e:
            log.error("❌ Failed to fetch symbols: %s", e)
            return []

    async def _run_stream(self):
        """الحلقة الرئيسية مع إصلاحات إعادة الاتصال"""
        self._is_running = True
        
        while self._is_running:
            try:
                symbols = self._get_symbols_to_watch()
                
                if not symbols:
                    log.info("⏸️ No open recommendations to watch. Checking again in 60 seconds.")
                    await asyncio.sleep(60)
                    continue

                log.info("🔌 Connecting to WebSocket for %d symbols (attempt %d/%d)", 
                        len(symbols), self._reconnect_attempts + 1, self._max_reconnect_attempts)
                
                # ✅ محاولة الاتصال مع timeout
                await asyncio.wait_for(
                    self._ws_client.combined_stream(symbols, self._price_handler),
                    timeout=30.0
                )

            except asyncio.TimeoutError:
                log.error("⏰ WebSocket connection timeout")
                self._reconnect_attempts += 1
                
            except (asyncio.CancelledError, KeyboardInterrupt):
                log.info("WebSocket task cancelled.")
                break
                
            except Exception as e:
                log.error("❌ WebSocket stream failed: %s", e)
                self._reconnect_attempts += 1

            # ✅ التحكم في إعادة الاتصال
            if self._reconnect_attempts >= self._max_reconnect_attempts:
                log.critical("💥 Max reconnection attempts reached. Stopping streamer.")
                break
                
            if self._reconnect_attempts > 0:
                wait_time = min(2 ** self._reconnect_attempts, 60)  # Exponential backoff
                log.warning("🔄 Reconnecting in %d seconds...", wait_time)
                await asyncio.sleep(wait_time)

    def start(self):
        """بدء الـ streamer مع تحسينات"""
        if self._task is None or self._task.done():
            log.info("🚀 Starting Price Streamer with enhanced reliability.")
            self._task = asyncio.create_task(self._run_stream())
        else:
            log.warning("⚠️ Price Streamer task is already running.")

    def stop(self):
        """إيقاف الـ streamer"""
        self._is_running = False
        
        if self._task and not self._task.done():
            log.info("🛑 Stopping Price Streamer task.")
            self._task.cancel()
        self._task = None