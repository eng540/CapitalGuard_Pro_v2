# src/capitalguard/application/services/alert_service.py (v25.7 - FINAL & THREAD-SAFE)
"""
AlertService - The re-architected, state-aware, and robust price monitoring engine.
This version includes a fix for the background thread startup sequence.
"""

import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional, Set
from decimal import Decimal
import time

if False:
    from .trade_service import TradeService
    from .price_service import PriceService

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer
from capitalguard.infrastructure.db.models import RecommendationStatusEnum

log = logging.getLogger(__name__)

WATCHDOG_INTERVAL_SECONDS = 60
WATCHDOG_STALE_THRESHOLD_SECONDS = 90

class AlertService:
    def __init__(
        self, 
        trade_service: "TradeService", 
        price_service: "PriceService", 
        repo: RecommendationRepository, 
        streamer: Optional[PriceStreamer] = None
    ):
        self.trade_service = trade_service
        self.price_service = price_service
        self.repo = repo
        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)
        
        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()
        
        self._last_price_seen_at: Dict[str, float] = {}
        
        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self):
        """Starts the AlertService and its background tasks in a separate thread."""
        if self._bg_thread and self._bg_thread.is_alive():
            log.warning("AlertService background thread already running.")
            return

        def _bg_runner():
            try:
                loop = asyncio.new_event_loop()
                self._bg_loop = loop
                asyncio.set_event_loop(loop)

                self._processing_task = loop.create_task(self._process_queue())
                self._index_sync_task = loop.create_task(self._run_index_sync())
                self._watchdog_task = loop.create_task(self._run_watchdog_check())
                
                if hasattr(self.streamer, "start"):
                    self.streamer.start(loop=loop)

                loop.run_forever()
            except Exception:
                log.exception("AlertService background runner crashed.")
            finally:
                if self._bg_loop and self._bg_loop.is_running():
                    self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        
        self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
        self._bg_thread.start()
        log.info("AlertService started in background thread.")

    async def build_triggers_index(self):
        log.info("Attempting to build in-memory trigger index...")
        try:
            with session_scope() as session:
                trigger_data = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.critical("CRITICAL: DB read failure during trigger index build.", exc_info=True)
            return

        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        for item in trigger_data:
            try:
                asset = item.get("asset")
                if not asset:
                    log.warning("Skipping trigger with empty asset: %s", item)
                    continue
                
                trigger_key = f"{asset.upper()}:{item.get('market', 'Futures')}"
                
                if trigger_key not in new_triggers:
                    new_triggers[trigger_key] = []
                
                new_triggers[trigger_key].append(item)

            except Exception:
                log.exception("Failed processing trigger item: %s", item)
        
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        
        total_items = len(trigger_data)
        log.info("✅ Trigger index rebuilt: %d trigger items across %d unique asset/market pairs.", total_items, len(new_triggers))

    async def _run_index_sync(self, interval_seconds: int = 300):
        log.info("Index sync task started (interval=%ss).", interval_seconds)
        while True:
            await asyncio.sleep(interval_seconds)
            await self.build_triggers_index()

    async def _run_watchdog_check(self):
        log.info("Price stream watchdog started (interval=%ss).", WATCHDOG_INTERVAL_SECONDS)
        stale_counters = {}
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
            try:
                if not self.streamer._task or self.streamer._task.done():
                    log.critical("CRITICAL: PriceStreamer task is not running! Attempting to restart.")
                    self.streamer.start(loop=self._bg_loop)

                async with self._triggers_lock:
                    watched_keys = list(self.active_triggers.keys())
                
                now = time.time()
                for key in watched_keys:
                    last_seen = self._last_price_seen_at.get(key, 0)
                    if now - last_seen > WATCHDOG_STALE_THRESHOLD_SECONDS:
                        stale_counters[key] = stale_counters.get(key, 0) + 1
                        if stale_counters[key] == 1 or stale_counters[key] % 5 == 0:
                            log.warning("Watchdog: Price stream for %s is stale. Fetching price manually.", key)
                        
                        try:
                            symbol, market = key.split(":")
                            price = await self.price_service.get_cached_price(symbol, market, force_refresh=True)
                            if price:
                                await self.price_queue.put((symbol, market, price, price))
                        except Exception:
                            log.exception("Watchdog: Failed to fetch manual price for stale key %s.", key)
                    elif key in stale_counters:
                        log.info("Watchdog: Price stream for %s has recovered.", key)
                        del stale_counters[key]
            except Exception:
                log.exception("An error occurred in the watchdog task.")

    async def _process_queue(self):
        log.info("AlertService queue processor started.")
        while True:
            try:
                symbol, market, low_price, high_price = await self.price_queue.get()
                trigger_key = f"{symbol.upper()}:{market}"
                self._last_price_seen_at[trigger_key] = time.time()
                
                await self.check_and_process_alerts(trigger_key, Decimal(str(low_price)), Decimal(str(high_price)))
                
                self.price_queue.task_done()
            except asyncio.CancelledError:
                log.info("Queue processor cancelled.")
                break
            except Exception:
                log.exception("Unexpected error in queue processor.")

    def stop(self):
        if hasattr(self.streamer, "stop"):
            self.streamer.stop()
        
        if self._bg_loop:
            tasks = [self._processing_task, self._index_sync_task, self._watchdog_task]
            for t in tasks:
                if t and not t.done():
                    self._bg_loop.call_soon_threadsafe(t.cancel)
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        
        if self._bg_thread:
            self._bg_thread.join(timeout=5.0)

        log.info("AlertService stopped and cleaned up.")

    def _is_price_condition_met(self, side: str, low_price: Decimal, high_price: Decimal, target_price: Decimal, condition_type: str) -> bool:
        side_upper = side.upper()
        cond = condition_type.upper()
        
        if side_upper == "LONG":
            if cond.startswith("TP"): return high_price >= target_price
            if cond == "SL": return low_price <= target_price
            if cond == "ENTRY": return low_price <= target_price
        elif side_upper == "SHORT":
            if cond.startswith("TP"): return low_price <= target_price
            if cond == "SL": return high_price >= target_price
            if cond == "ENTRY": return high_price >= target_price
        return False

    async def check_and_process_alerts(self, trigger_key: str, low_price: Decimal, high_price: Decimal):
        async with self._triggers_lock:
            triggers_for_key = self.active_triggers.get(trigger_key, [])
        
        if not triggers_for_key:
            return

        processed_item_ids = set()

        for trigger in triggers_for_key:
            item_id = trigger["id"]
            if item_id in processed_item_ids:
                continue

            status = trigger["status"]
            side = trigger["side"]
            
            try:
                if status == RecommendationStatusEnum.PENDING:
                    entry_price = trigger["entry"]
                    sl_price = trigger["stop_loss"]

                    if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        log.warning("Invalidation HIT for PENDING Rec #%s: SL hit before entry.", item_id)
                        await self.trade_service.process_invalidation_event(item_id)
                        processed_item_ids.add(item_id)
                        continue

                    if self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                        log.info("Activation HIT for PENDING Rec #%s.", item_id)
                        await self.trade_service.process_activation_event(item_id)
                        processed_item_ids.add(item_id)
                        continue

                elif status == RecommendationStatusEnum.ACTIVE:
                    sl_price = trigger["stop_loss"]
                    
                    if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        log.info("SL HIT for ACTIVE item #%s.", item_id)
                        await self.trade_service.process_sl_hit_event(item_id, sl_price)
                        processed_item_ids.add(item_id)
                        continue

                    for i, target in enumerate(trigger["targets"], 1):
                        event_key = f"TP{i}_HIT"
                        if event_key in trigger["processed_events"]:
                            continue
                        
                        target_price = Decimal(str(target['price']))
                        if self._is_price_condition_met(side, low_price, high_price, target_price, "TP"):
                            log.info("TP%d HIT for ACTIVE item #%s.", i, item_id)
                            await self.trade_service.process_tp_hit_event(item_id, i, target_price)
                            processed_item_ids.add(item_id)
            
            except Exception as e:
                log.error("Error processing trigger for item #%s: %s", item_id, e, exc_info=True)
                processed_item_ids.add(item_id)

#END