# src/capitalguard/infrastructure/sched/price_streamer.py (v15.5 - Final v3.0 Compatible)
import asyncio
import logging
from typing import List, Set

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger("capitalguard.streamer")

class PriceStreamer:
    def __init__(self, queue: asyncio.Queue, repo: RecommendationRepository):
        self._queue = queue
        self._repo = repo
        self._ws_client = BinanceWS()
        self._active_symbols: Set[str] = set()
        self._task: asyncio.Task = None

    async def _get_symbols_to_watch(self) -> List[str]:
        """
        ✅ CORRECTED: Now uses the correct repository method to get all symbols
        that have active triggers (from both UserTrades and pending Recommendations).
        """
        with session_scope() as session:
            trigger_items = self._repo.list_all_active_triggers_data(session)
            return list({item['asset'] for item in trigger_items})

    async def _run_stream(self):
        while True:
            try:
                symbols = await self._get_symbols_to_watch()
                if not symbols:
                    log.info("No open positions to watch. Checking again in 60 seconds.")
                    await asyncio.sleep(60)
                    continue

                if set(symbols) != self._active_symbols:
                    self._active_symbols = set(symbols)
                    log.info(f"Symbol list changed. Connecting to stream for {len(self._active_symbols)} symbols: {self._active_symbols}")
                    # This call is blocking and will run forever until the connection drops
                    await self._ws_client.combined_stream(list(self._active_symbols), self._price_handler)
                else:
                    # If symbols haven't changed, we are likely in a reconnect loop.
                    # Wait before trying to connect again.
                    log.debug("Symbol list unchanged. Waiting before next check.")
                    await asyncio.sleep(60)

            except (asyncio.CancelledError, KeyboardInterrupt):
                log.info("Price streamer task cancelled.")
                break
            except Exception:
                log.exception("WebSocket stream failed. Reconnecting in 15 seconds...")
                self._active_symbols = set() # Force a reconnect with fresh symbols
                await asyncio.sleep(15)

    async def _price_handler(self, symbol: str, low_price: float, high_price: float):
        try:
            await self._queue.put((symbol, low_price, high_price))
        except Exception:
            log.exception("Failed to put price update into the queue.")

    def start(self):
        if self._task is None or self._task.done():
            log.info("Starting Price Streamer background task.")
            self._task = asyncio.create_task(self._run_stream())
        else:
            log.warning("Price Streamer task is already running.")

    def stop(self):
        if self._task and not self._task.done():
            log.info("Stopping Price Streamer background task.")
            self._task.cancel()
        self._task = None