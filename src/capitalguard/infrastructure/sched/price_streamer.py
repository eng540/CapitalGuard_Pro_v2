# --- START OF NEW, ARCHITECTURALLY-CORRECT FILE (Version 12.0.0) ---
# src/capitalguard/infrastructure/sched/price_streamer.py

import asyncio
import logging
from typing import List, Set

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger("capitalguard.streamer")

class PriceStreamer:
    """
    A dedicated, high-performance component responsible for one thing only:
    streaming live prices from Binance WebSocket and putting them into a shared queue.
    It does NOT contain any business logic.
    """
    def __init__(self, queue: asyncio.Queue, repo: RecommendationRepository):
        self._queue = queue
        self._repo = repo
        self._ws_client = BinanceWS()
        self._active_symbols: Set[str] = set()
        self._task: asyncio.Task = None

    async def _price_handler(self, symbol: str, price: float, _raw_data: dict):
        """Callback function for the WebSocket client. Puts price data into the queue."""
        try:
            await self._queue.put((symbol, price))
        except Exception:
            log.exception("Failed to put price update into the queue.")

    async def _get_symbols_to_watch(self) -> List[str]:
        """Fetches the current set of unique symbols for all open recommendations."""
        with SessionLocal() as session:
            open_recs = self._repo.list_open(session)
            return list({rec.asset.value for rec in open_recs})

    async def _run_stream(self):
        """The main loop that manages the WebSocket connection."""
        while True:
            try:
                symbols = await self._get_symbols_to_watch()
                if not symbols:
                    log.info("No open recommendations to watch. Checking again in 60 seconds.")
                    await asyncio.sleep(60)
                    continue

                # Only reconnect if the set of symbols has changed.
                if set(symbols) != self._active_symbols:
                    self._active_symbols = set(symbols)
                    log.info(f"Symbol list changed. Connecting to stream for {len(self._active_symbols)} symbols.")
                    # The combined_stream function will run indefinitely until it disconnects.
                    await self._ws_client.combined_stream(symbols, self._price_handler)
                else:
                    # If symbols are the same, just wait before checking again.
                    # This prevents constant DB queries if the stream is stable.
                    await asyncio.sleep(60)

            except (asyncio.CancelledError, KeyboardInterrupt):
                log.info("Price streamer task cancelled.")
                break
            except Exception:
                log.exception("WebSocket stream failed. Reconnecting in 15 seconds...")
                self._active_symbols = set() # Force reconnect on next iteration
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

# --- END OF NEW, ARCHITECTURALLY-CORRECT FILE ---