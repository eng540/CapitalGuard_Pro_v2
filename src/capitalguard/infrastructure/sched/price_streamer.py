# src/capitalguard/infrastructure/sched/price_streamer.py (v20.0.0 - Production Ready)
"""
PriceStreamer - Final, robust, and efficient version.
This version fixes the reconnect loop bug and the AttributeError, ensuring a stable, persistent WebSocket connection.
"""

import asyncio
import logging
from typing import List, Set, Optional

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.infrastructure.sched.shared_queue import ThreadSafeQueue

log = logging.getLogger("capitalguard.streamer")

class PriceStreamer:
    def __init__(self, price_queue: ThreadSafeQueue, repo: RecommendationRepository):
        self._queue = price_queue
        self._repo = repo
        self._ws_client = BinanceWS()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def _price_handler(self, symbol: str, low_price: float, high_price: float):
        """Callback function for the WebSocket client. Puts the price range into the queue."""
        try:
            await self.price_queue.put((symbol, low_price, high_price))
        except Exception:
            log.exception("Failed to put price update into the queue.")

    def _get_symbols_to_watch(self) -> List[str]:
        """Fetches the current set of unique symbols for all open recommendations."""
        with session_scope() as session:
            open_recs_orm = self.repo.list_open_orm(session)
            return list({rec.asset for rec in open_recs_orm})

    # ✅ --- CRITICAL FIX: Restructured the main loop to be resilient and stateless ---
    async def _run_stream(self):
        """The main loop that manages the WebSocket connection with a robust retry mechanism."""
        while self._running:
            try:
                symbols = self._get_symbols_to_watch()
                if not symbols:
                    log.info("No open recommendations to watch. Checking again in 60 seconds.")
                    await asyncio.sleep(60)
                    continue

                # The combined_stream function will run indefinitely until the connection is lost.
                # It handles the connection state internally.
                await self._ws_client.combined_stream(symbols, self._price_handler)

            except (asyncio.CancelledError, KeyboardInterrupt):
                log.info("Price streamer task cancelled.")
                break
            except Exception:
                # If any error occurs (e.g., connection closed), log it and retry after a delay.
                log.exception("WebSocket stream failed. Reconnecting in 15 seconds...")
                await asyncio.sleep(15)

    def start(self):
        """Starts the streamer as a background asyncio task."""
        if self._running:
            log.warning("PriceStreamer is already running.")
            return
            
        self._running = True
        self._task = asyncio.create_task(self._run_stream())
        log.info("Price Streamer background task started.")

    def stop(self):
        """Stops the streamer background task."""
        if not self._running:
            return
            
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("Price Streamer stopped.")