# src/capitalguard/infrastructure/sched/price_streamer.py (v19.0.5)
"""
PriceStreamer with enhanced logging and error handling.
"""

import asyncio
import logging
from typing import Set, Dict, Any
from datetime import datetime

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

    async def _get_active_symbols(self) -> Set[str]:
        """Fetches active symbols from the repository."""
        try:
            symbols = set()
            with self.repo.session_scope() as session:
                active_recs = self.repo.list_all_active_triggers_data(session)
                for rec in active_recs:
                    asset = (rec.get("asset") or "").strip().upper()
                    if asset:
                        symbols.add(asset)
            return symbols
        except Exception as e:
            log.error("Error fetching active symbols: %s", e)
            return set()

    async def _update_symbols(self):
        """Updates the list of symbols to monitor."""
        new_symbols = await self._get_active_symbols()
        if new_symbols != self.symbols:
            old_count = len(self.symbols)
            self.symbols = new_symbols
            log.info("Symbol list changed. Connecting to stream for %d symbols. (Was: %d)", 
                    len(self.symbols), old_count)
            return True
        return False

    async def _run_stream(self):
        """Main streaming loop."""
        log.info("PriceStreamer started monitoring symbols: %s", list(self.symbols))
        
        while self._running:
            try:
                # ✅ تحديث قائمة الرموز بشكل دوري
                if await self._update_symbols():
                    if self.symbols:
                        await self.ws_client.connect(list(self.symbols))
                    else:
                        log.warning("No active symbols to monitor")
                        await asyncio.sleep(10)
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
                        
                        # ✅ إرسال البيانات إلى الـ queue مع logging تفصيلي
                        await self.price_queue.put((symbol, low, high))
                        
                        if self._message_count % 50 == 0:  # تسجيل كل 50 رسالة
                            log.info("Streamed %d prices. Latest: %s (L:%.6f H:%.6f)", 
                                    self._message_count, symbol, low, high)
                        else:
                            log.debug("Price streamed: %s (L:%.6f H:%.6f) - Total: %d", 
                                     symbol, low, high, self._message_count)
                    
            except asyncio.CancelledError:
                log.info("PriceStreamer cancelled")
                break
            except Exception as e:
                log.error("Error in price streamer: %s", e)
                await asyncio.sleep(1)

    def start(self):
        """Starts the price streaming service."""
        if self._running:
            log.warning("PriceStreamer already running")
            return
            
        self._running = True
        try:
            self._task = asyncio.create_task(self._run_stream())
            log.info("✅ PriceStreamer background task started")
        except RuntimeError as e:
            log.error("Failed to create task: %s", e)
            self._running = False

    def stop(self):
        """Stops the price streaming service."""
        self._running = False
        if self._task:
            self._task.cancel()
        self.ws_client.disconnect()
        log.info("PriceStreamer stopped. Total messages processed: %d", self._message_count)

    async def get_status(self) -> Dict[str, Any]:
        """Returns the current status of the streamer."""
        return {
            "running": self._running,
            "symbols_monitored": list(self.symbols),
            "symbols_count": len(self.symbols),
            "messages_processed": self._message_count,
            "websocket_connected": self.ws_client.connected,
        }