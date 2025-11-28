#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---
# src/capitalguard/infrastructure/sched/price_streamer.py
# Version: v3.1.0 - Enriched Data Stream
# ✅ THE FIX: Inject Source & Timestamp into the payload.
# 🎯 IMPACT: Enables Arbitration & Data Quality checks in AlertService v30.
#    Payload format: (symbol, market, low, high, source, timestamp)

import asyncio
import logging
import os
import time
from typing import List, Set, Dict, Optional, Callable, Awaitable

# Import both clients
from capitalguard.infrastructure.market.ws_client import BinanceWS, BybitWS
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger("capitalguard.streamer")

class PriceStreamer:
    def __init__(self, queue: asyncio.Queue, repo: RecommendationRepository):
        self._queue = queue
        self._repo = repo
        
        # Initialize Adapters
        self._binance = BinanceWS()
        self._bybit = BybitWS()
        
        self._active_symbols_by_market: Dict[str, Set[str]] = {}
        self._task: Optional[asyncio.Task] = None

    async def _get_symbols_to_watch(self) -> List[str]:
        """Fetches all unique symbols to be watched."""
        symbols = set()
        try:
            with session_scope() as session:
                trigger_items = self._repo.list_all_active_triggers_data(session)
                for item in trigger_items:
                    asset = item.get("asset")
                    if asset:
                        symbols.add(asset)
        except Exception as e:
            log.error(f"Error fetching symbols: {e}")
        return list(symbols)

    def _create_source_handler(self, source_name: str) -> Callable[[str, float, float], Awaitable[None]]:
        """
        Creates a specialized handler for a specific exchange source.
        Injects 'source' and 'timestamp' into the queue payload.
        """
        async def wrapped_handler(symbol: str, low: float, high: float):
            try:
                # 6-Element Tuple for AlertService v30
                # (symbol, market, low, high, source, timestamp)
                ts = int(time.time())
                await self._queue.put((symbol, "Futures", low, high, source_name, ts))
            except Exception:
                pass
        
        return wrapped_handler

    async def _run_single_stream(self, name: str, stream_coro):
        """Helper to run a single exchange stream with infinite retry loop."""
        while True:
            try:
                await stream_coro
            except asyncio.CancelledError:
                log.info(f"🛑 {name} stream cancelled.")
                break
            except Exception as e:
                log.warning(f"⚠️ {name} stream disconnected: {e}. Reconnecting in 10s...")
                await asyncio.sleep(10)

    async def _run_aggregator(self):
        """
        The main aggregator loop.
        1. Monitors DB for new symbols.
        2. Spawns connection tasks for Binance and Bybit with ENRICHED handlers.
        3. Restarts connections if symbol list changes.
        """
        current_tasks = []
        
        while True:
            try:
                symbols_to_watch = await self._get_symbols_to_watch()

                if not symbols_to_watch:
                    log.info("💤 No active positions. Idling...")
                    if current_tasks:
                        for t in current_tasks: t.cancel()
                        current_tasks = []
                    await asyncio.sleep(60)
                    continue

                # Check if symbols changed
                current_set = set(self._active_symbols_by_market.get("ALL", []))
                new_set = set(symbols_to_watch)

                if new_set != current_set:
                    log.info(f"🔄 Symbol list updated: {len(new_set)} symbols. Restarting streams...")
                    self._active_symbols_by_market["ALL"] = new_set
                    
                    # Cancel old streams
                    for t in current_tasks: t.cancel()
                    if current_tasks:
                        await asyncio.gather(*current_tasks, return_exceptions=True)
                    
                    current_tasks = []

                    # --- 🚀 LAUNCH MULTI-HEAD STREAMS WITH ENRICHED HANDLERS ---
                    
                    # 1. Binance Task
                    # Create a specific handler labeled "BINANCE"
                    binance_handler = self._create_source_handler("BINANCE")
                    task_binance = asyncio.create_task(
                        self._run_single_stream(
                            "Binance", 
                            self._binance.combined_stream(symbols_to_watch, binance_handler)
                        )
                    )
                    current_tasks.append(task_binance)

                    # 2. Bybit Task
                    # Create a specific handler labeled "BYBIT"
                    bybit_handler = self._create_source_handler("BYBIT")
                    task_bybit = asyncio.create_task(
                        self._run_single_stream(
                            "Bybit",
                            self._bybit.stream(symbols_to_watch, bybit_handler)
                        )
                    )
                    current_tasks.append(task_bybit)
                    
                    log.info("✅ Aggregator running: [Binance] + [Bybit] active (Enriched Data Mode).")

                await asyncio.sleep(60) # Check for new symbols every minute

            except (asyncio.CancelledError, KeyboardInterrupt):
                log.info("Aggregator stopping...")
                for t in current_tasks: t.cancel()
                break
            except Exception:
                log.exception("Aggregator main loop error.")
                await asyncio.sleep(30)

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        if self._task and not self._task.done():
            return
        
        _loop = loop or asyncio.get_running_loop()
        log.info("🚀 Starting Multi-Exchange Price Streamer (v3.1).")
        self._task = _loop.create_task(self._run_aggregator())

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---