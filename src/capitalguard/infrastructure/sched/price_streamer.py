# src/capitalguard/infrastructure/sched/price_streamer.py (v19.0.5 - الإصلاح النهائي)
"""
PriceStreamer with enhanced logging and error handling.
"""

import asyncio
import logging
from typing import Set, Dict, Any, Optional
from datetime import datetime

from capitalguard.infrastructure.db.uow import session_scope  # ✅ الاستيراد الصحيح
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.market.ws_client import BinanceWebSocketClient

log = logging.getLogger(__name__)

class PriceStreamer:
    """Streams real-time price data from Binance WebSocket."""
    
    def __init__(self, price_queue: asyncio.Queue, repo: RecommendationRepository):
        self.price_queue = price_queue
        self.repo = repo
        self.symbols: Set[str] = set()
        self.ws_client = BinanceWebSocketClient()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._message_count = 0
        self._symbol_update_interval = 30  # تحديث الرموز كل 30 ثانية
        self._last_symbol_update = 0

    async def _get_active_symbols(self) -> Set[str]:
        """Fetches active symbols from the repository."""
        try:
            symbols = set()
            # ✅ استخدام session_scope الصحيح من uow
            with session_scope() as session:
                active_recs = self.repo.list_all_active_triggers_data(session)
                for rec in active_recs:
                    asset = (rec.get("asset") or "").strip().upper()
                    if asset:
                        symbols.add(asset)
            log.info("✅ Successfully fetched %d active symbols: %s", len(symbols), list(symbols))
            return symbols
        except Exception as e:
            log.error("❌ Error fetching active symbols: %s", e)
            return set()

    async def _update_symbols(self):
        """Updates the list of symbols to monitor."""
        current_time = datetime.now().timestamp()
        
        # ✅ تحديث الرموز فقط إذا مر وقت كافٍ منذ آخر تحديث
        if current_time - self._last_symbol_update < self._symbol_update_interval:
            return False
            
        try:
            new_symbols = await self._get_active_symbols()
            if new_symbols != self.symbols:
                old_count = len(self.symbols)
                self.symbols = new_symbols
                self._last_symbol_update = current_time
                log.info("🔄 Symbol list changed. Now monitoring %d symbols. (Was: %d)", 
                        len(self.symbols), old_count)
                return True
            return False
        except Exception as e:
            log.error("Error updating symbols: %s", e)
            return False

    async def _run_stream(self):
        """Main streaming loop."""
        log.info("🎯 PriceStreamer started with initial symbols: %s", list(self.symbols))
        
        # ✅ الحصول على الرموز الأولية مباشرة عند البدء
        await self._update_symbols()
        
        while self._running:
            try:
                # ✅ تحديث قائمة الرموز بشكل دوري
                symbols_updated = await self._update_symbols()
                
                if not self.symbols:
                    log.warning("⏸️ No active symbols to monitor. Waiting...")
                    await asyncio.sleep(10)
                    continue

                # ✅ الاتصال بـ WebSocket إذا لم يكن متصلاً أو تغيرت الرموز
                if not self.ws_client.connected or symbols_updated:
                    if self.symbols:
                        await self.ws_client.connect(list(self.symbols))
                    else:
                        await asyncio.sleep(5)
                        continue

                # ✅ استقبال الرسائل من WebSocket
                message = await self.ws_client.receive_message()
                if message:
                    self._message_count += 1
                    
                    symbol = message.get('s')
                    kline = message.get('k')
                    
                    if kline and kline.get('x'):  # إذا كانت الشمعة مغلقة
                        low = float(kline['l'])
                        high = float(kline['h'])
                        
                        # ✅ إرسال البيانات إلى الـ queue
                        await self.price_queue.put((symbol, low, high))
                        
                        # ✅ تسجيل تفصيلي للأسعار
                        if self._message_count % 50 == 0:  # تسجيل كل 50 رسالة
                            log.info("📊 Streamed %d prices. Latest: %s (L:%.6f H:%.6f)", 
                                    self._message_count, symbol, low, high)
                        elif self._message_count <= 10:  # تسجيل أول 10 أسعار
                            log.debug("📍 Price %d: %s (L:%.6f H:%.6f)", 
                                     self._message_count, symbol, low, high)
                    
                # ✅ فحص صحة الاتصال بشكل دوري
                if self._message_count % 100 == 0:
                    is_healthy = await self.ws_client.health_check()
                    if not is_healthy:
                        log.warning("🔌 WebSocket connection unhealthy, reconnecting...")
                        self.ws_client.disconnect()
                        
            except asyncio.CancelledError:
                log.info("🛑 PriceStreamer cancelled")
                break
            except Exception as e:
                log.error("💥 Error in price streamer: %s", e)
                await asyncio.sleep(1)  # انتظار قبل إعادة المحاولة

    def start(self):
        """Starts the price streaming service."""
        if self._running:
            log.warning("⚠️ PriceStreamer already running")
            return
            
        self._running = True
        try:
            self._task = asyncio.create_task(self._run_stream())
            log.info("✅ PriceStreamer background task started successfully")
        except RuntimeError as e:
            log.error("❌ Failed to create PriceStreamer task: %s", e)
            self._running = False
        except Exception as e:
            log.error("❌ Unexpected error starting PriceStreamer: %s", e)
            self._running = False

    def stop(self):
        """Stops the price streaming service."""
        if not self._running:
            return
            
        self._running = False
        if self._task:
            self._task.cancel()
        self.ws_client.disconnect()
        log.info("🛑 PriceStreamer stopped. Total messages processed: %d", self._message_count)

    async def get_status(self) -> Dict[str, Any]:
        """Returns the current status of the streamer."""
        return {
            "running": self._running,
            "symbols_monitored": list(self.symbols),
            "symbols_count": len(self.symbols),
            "messages_processed": self._message_count,
            "websocket_connected": self.ws_client.connected,
            "queue_size": self.price_queue.qsize(),
        }

    def is_healthy(self) -> bool:
        """Checks if the streamer is healthy."""
        return (self._running and 
                self.ws_client.connected and 
                len(self.symbols) > 0 and 
                self._message_count > 0)