# src/capitalguard/infrastructure/sched/price_streamer.py (v19.0.7 - Production Ready)
"""
PriceStreamer - Final, robust, and efficient version.
This version fixes the reconnect loop bug, ensuring a stable, persistent WebSocket connection.
It now only queries the database for symbols when the connection is first established or after a disconnect.
"""

import asyncio
import logging
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

    async def _price_handler(self, symbol: str, low_price: float, high_price: float):
        """Callback function for the WebSocket client. Puts the price range into the queue."""
        try:
            await self._queue.put((symbol, low_price, high_price))
        except Exception:
            log.exception("Failed to put price update into the queue.")

    def _get_symbols_to_watch(self) -> List[str]:
        """Fetches the current set of unique symbols for all open recommendations."""
        with SessionLocal() as session:
            open_recs_orm = self._repo.list_open_orm(session)
            return list({rec.asset for rec in open_recs_orm})

    # ✅ --- CRITICAL FIX: Restructured the main loop to be resilient and efficient ---
    async def _run_stream(self):
        """The main loop that manages the WebSocket connection with a robust retry mechanism."""
        while True:
            try:
                symbols = self._get_symbols_to_watch()
                if not symbols:
                    log.info("No open recommendations to watch. Checking again in 60 seconds.")
                    await asyncio.sleep(60)
                    continue

                log.info(f"Connecting to stream for {len(symbols)} symbols.")
                # This call will run indefinitely until the connection is lost or an error occurs.
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
        if self._task is None or self._task.done():
            log.info("Starting Price Streamer background task.")
            self._task = asyncio.create_task(self._run_stream())
        else:
            log.warning("Price Streamer task is already running.")

    def stop(self):
        """Stops the streamer background task."""
        if self._task and not self._task.done():
            log.info("Stopping Price Streamer background task.")
            self._task.cancel()
        self._task = None