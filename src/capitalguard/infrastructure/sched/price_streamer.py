# --- START OF ENHANCED VERSION: src/capitalguard/infrastructure/sched/price_streamer.py ---
# File: src/capitalguard/infrastructure/sched/price_streamer.py
# Version: v5.0.0-ENHANCED (Combined Best Features)
# ✅ Combines caching efficiency + improved task management + better market detection

import asyncio
import logging
import json
from typing import Set, Dict, List, Optional
from decimal import Decimal
from websockets.exceptions import ConnectionClosed
from capitalguard.infrastructure.market.ws_client import BinanceWSClient
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.db.models import RecommendationStatusEnum, UserTradeStatusEnum, Recommendation, UserTrade
from capitalguard.infrastructure.core_engine import core_cache

log = logging.getLogger(__name__)

class PriceStreamer:
    def __init__(self, price_queue: asyncio.Queue, repo: RecommendationRepository):
        self.price_queue = price_queue
        self.repo = repo
        self.client = BinanceWSClient()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # Cache active symbols to avoid DB spam with improved market detection
        self._active_symbols_by_market: Dict[str, Set[str]] = {"Futures": set(), "Spot": set()}

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        """
        Starts the streamer as a background asyncio task.
        Uses explicit loop if provided (for background threads).
        """
        if self._running: 
            log.warning("PriceStreamer is already running.")
            return
            
        self._running = True
        try:
            # ✅ ENHANCED: Use provided loop or get running loop safely
            if loop:
                self._task = loop.create_task(self._run_stream())
            else:
                self._task = asyncio.create_task(self._run_stream())
            log.info("PriceStreamer started successfully.")
        except RuntimeError as e:
            log.error(f"Failed to start PriceStreamer: {e}")
            self._running = False

    def stop(self):
        """Stops the streamer with proper task cancellation handling."""
        self._running = False
        if self._task and not self._task.done():
            log.info("Stopping PriceStreamer background task.")
            self._task.cancel()
        self._task = None

    async def _get_symbols_to_watch(self) -> Dict[str, Set[str]]:
        """Efficiently fetch distinct active symbols from DB with caching"""
        symbols_by_market = {"Futures": set(), "Spot": set()}
        try:
            # ✅ ENHANCED: Use cache to reduce DB load (30 seconds TTL)
            cached = await core_cache.get("active_watch_symbols")
            if cached: 
                return cached

            with session_scope() as session:
                # 1. Active Recommendations with market detection
                recs = session.query(Recommendation.asset, Recommendation.market).filter(
                    Recommendation.status.in_([RecommendationStatusEnum.ACTIVE, RecommendationStatusEnum.PENDING])
                ).all()
                
                # 2. Active UserTrades
                trades = session.query(UserTrade.asset, UserTrade.market).filter(
                    UserTrade.status.in_([UserTradeStatusEnum.ACTIVATED, UserTradeStatusEnum.PENDING_ACTIVATION])
                ).all()

                # ✅ ENHANCED: Better market detection logic
                for r in recs:
                    market = self._determine_market(r.market)
                    symbols_by_market[market].add(r.asset)
                
                for t in trades:
                    market = self._determine_market(t.market)
                    symbols_by_market[market].add(t.asset)

            # Update cache
            await core_cache.set("active_watch_symbols", symbols_by_market, ttl=30)
            return symbols_by_market
        except Exception as e:
            log.error(f"Symbol fetch error: {e}")
            return self._active_symbols_by_market  # Return last known state

    def _determine_market(self, market: Optional[str]) -> str:
        """Enhanced market detection logic"""
        if not market:
            return "Futures"
        market_lower = market.lower()
        if "spot" in market_lower:
            return "Spot"
        return "Futures"

    async def _handle_price(self, symbol: str, low: float, high: float, close: float):
        """Enhanced price handler with market context and cache updates"""
        try:
            # ✅ ENHANCED: Determine market for each symbol
            market = "Futures"
            if symbol in self._active_symbols_by_market.get("Spot", set()):
                market = "Spot"
            
            # Put into queue for AlertService with complete data
            await self.price_queue.put({
                "symbol": symbol, 
                "market": market,
                "low": low, 
                "high": high, 
                "close": close,
                "ts": int(asyncio.get_event_loop().time())
            })
            
            # ✅ ENHANCED: Update Cache for WebApp instant access with market prefix
            cache_key = f"price:{market.upper()}:{symbol}"
            await core_cache.set(cache_key, close, ttl=60)
            
        except Exception as e:
            log.error(f"Price handling error for {symbol}: {e}")

    async def _run_stream(self):
        """Enhanced main streaming loop with intelligent reconnection"""
        while self._running:
            try:
                symbols_map = await self._get_symbols_to_watch()
                # Flatten symbols list
                all_symbols = list(symbols_map["Futures"] | symbols_map["Spot"])
                
                if not all_symbols:
                    # ✅ ENHANCED: Longer sleep when no symbols (reduces DB load)
                    log.info("No active symbols to watch. Checking again in 30 seconds.")
                    await asyncio.sleep(30)
                    continue
                
                # ✅ ENHANCED: Check if symbol list actually changed
                current_symbols = self._active_symbols_by_market["Futures"] | self._active_symbols_by_market["Spot"]
                if set(all_symbols) == current_symbols:
                    # Symbols unchanged, maintain connection
                    await asyncio.sleep(30)
                    continue
                
                # Update active symbols and connect
                self._active_symbols_by_market = symbols_map
                log.info(f"Connecting stream for {len(all_symbols)} symbols "
                        f"(Futures: {len(symbols_map['Futures'])}, Spot: {len(symbols_map['Spot'])})")
                
                # Connection logic inside ws_client needs to be robust
                await self.client.combined_stream(all_symbols, self._handle_price)
                
            except asyncio.CancelledError:
                log.info("PriceStreamer task was cancelled.")
                break
            except ConnectionClosed as e:
                log.error(f"WebSocket connection closed: {e}. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)
            except Exception as e:
                log.error(f"Unexpected stream error: {e}. Restarting in 10 seconds...")
                await asyncio.sleep(10)
                
        log.info("PriceStreamer main loop exited.")

# --- END OF ENHANCED VERSION ---