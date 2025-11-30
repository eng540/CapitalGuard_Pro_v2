# src/capitalguard/infrastructure/sched/price_streamer.py (v25.6 - Loop-Aware Startup)
"""
A dedicated component for streaming live prices from Binance WebSocket.
This version is context-aware and includes a fix for starting tasks in a new loop.
"""

import asyncio
import logging
from typing import List, Set, Dict, Optional

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger("capitalguard.streamer")

class PriceStreamer:
    def __init__(self, queue: asyncio.Queue, repo: RecommendationRepository):
        self._queue = queue
        self._repo = repo
        self._ws_client = BinanceWS()
        self._active_symbols_by_market: Dict[str, Set[str]] = {}
        self._task: Optional[asyncio.Task] = None

    async def _get_symbols_to_watch(self) -> Dict[str, Set[str]]:
        """Fetches all symbols to be watched, grouped by market."""
        symbols_by_market: Dict[str, Set[str]] = {"Futures": set(), "Spot": set()}
        with session_scope() as session:
            trigger_items = self._repo.list_all_active_triggers_data(session)
            for item in trigger_items:
                market = item.get("market", "Futures") or "Futures"
                asset = item.get("asset")
                if "spot" in market.lower():
                    symbols_by_market["Spot"].add(asset)
                else:
                    symbols_by_market["Futures"].add(asset)
        return symbols_by_market

    async def _run_stream(self):
        """The main loop that manages the WebSocket connection."""
        while True:
            try:
                symbols_by_market = await self._get_symbols_to_watch()
                symbols_to_watch = list(symbols_by_market["Futures"] | symbols_by_market["Spot"])

                if not symbols_to_watch:
                    log.info("No open positions to watch. Checking again in 60 seconds.")
                    await asyncio.sleep(60)
                    continue

                current_watched_set = set(self._active_symbols_by_market.get("Futures", set()) | self._active_symbols_by_market.get("Spot", set()))

                if set(symbols_to_watch) != current_watched_set:
                    self._active_symbols_by_market = symbols_by_market
                    log.info(f"Symbol list changed. Connecting to stream for {len(symbols_to_watch)} symbols.")
                    await self._ws_client.combined_stream(symbols_to_watch, self._price_handler)
                else:
                    await asyncio.sleep(60)

            except (asyncio.CancelledError, KeyboardInterrupt):
                log.info("Price streamer task cancelled.")
                break
            except Exception:
                log.exception("WebSocket stream failed. Reconnecting in 15 seconds...")
                self._active_symbols_by_market = {}
                await asyncio.sleep(15)

    async def _price_handler(self, symbol: str, low_price: float, high_price: float):
        """Callback for the WebSocket client, includes market context."""
        try:
            market = "Futures"
            if symbol in self._active_symbols_by_market.get("Spot", set()):
                market = "Spot"
            await self._queue.put((symbol, market, low_price, high_price))
        except Exception:
            log.exception("Failed to put price update into the queue.")

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        """
        Starts the streamer as a background asyncio task, using an explicit loop if provided.
        """
        if self._task and not self._task.done():
            log.warning("Price Streamer task is already running.")
            return
        
        # ✅ THE FIX: Use the provided loop from the background thread to create the task.
        # If no loop is provided, it falls back to the current running loop.
        _loop = loop or asyncio.get_running_loop()
        log.info("Starting Price Streamer background task.")
        self._task = _loop.create_task(self._run_stream())

    def stop(self):
        """Stops the streamer background task."""
        if self._task and not self._task.done():
            log.info("Stopping Price Streamer background task.")
            self._task.cancel()
        self._task = None