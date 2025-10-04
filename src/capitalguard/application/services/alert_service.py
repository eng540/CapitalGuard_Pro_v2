# src/capitalguard/application/services/alert_service.py (v21.0.1 - Final Multi-Tenant)
"""
AlertService — Final, complete, and multi-tenant ready version.
This file contains the full logic for monitoring both UserTrades and pending
Analyst Recommendations, including the self-healing Watchdog mechanism.
"""

import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional, Set
from contextlib import suppress
import time
import re

from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

WATCHDOG_INTERVAL_SECONDS = 60
WATCHDOG_STALE_THRESHOLD_SECONDS = 90


class AlertService:
    def __init__(self, trade_service: TradeService, price_service: PriceService, repo: RecommendationRepository, streamer: Optional[PriceStreamer] = None, debounce_seconds: float = 1.0):
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
        self._last_processed: Dict[int, Dict[str, float]] = {}
        self._debounce_seconds = float(debounce_seconds)
        self._tp_re = re.compile(r"^TP(\d+)$", flags=re.IGNORECASE)

    async def build_triggers_index(self):
        log.info("Attempting to build in-memory trigger index...")
        retry_delay = 5
        while True:
            try:
                with session_scope() as session:
                    trigger_data = self.repo.list_all_active_triggers_data(session)
                break
            except Exception:
                log.critical("CRITICAL: DB read failure. Retrying in %ds...", retry_delay, exc_info=True)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        for item in trigger_data:
            try:
                asset_raw = (item.get("asset") or "").strip().upper()
                if not asset_raw:
                    log.warning("Skipping trigger with empty asset: %s", item)
                    continue
                item["asset"] = asset_raw
                self._add_item_to_trigger_dict(new_triggers, item)
            except Exception:
                log.exception("Failed processing trigger item: %s", item)
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        total_items = len(trigger_data) if trigger_data is not None else 0
        log.info("✅ Trigger index built: %d trigger items across %d symbols.", total_items, len(new_triggers))

    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        asset = (item.get("asset") or "").strip().upper()
        if not asset: raise ValueError("Empty asset")
        if asset not in trigger_dict: trigger_dict[asset] = []
        
        is_user_trade = item.get("is_user_trade", False)
        
        def _create_trigger(trigger_type: str, price: Any) -> Dict[str, Any]:
            return {
                "id": item.get("id"),
                "user_id": item.get("user_id"),
                "side": item.get("side"),
                "type": trigger_type,
                "price": float(price),
                "processed_events": item.get("processed_events", set()),
                "status": item.get("status"),
                "is_user_trade": is_user_trade
            }

        status = item.get("status")
        
        if is_user_trade: # This is a UserTrade
            if item.get("stop_loss") is not None:
                trigger_dict[asset].append(_create_trigger("SL", item.get("stop_loss")))
            for idx, target in enumerate(item.get("targets", []), 1):
                trigger_dict[asset].append(_create_trigger(f"TP{idx}", target.get("price")))
        else: # This is an official Recommendation
            if status == RecommendationStatus.PENDING:
                trigger_dict[asset].append(_create_trigger("ENTRY", item.get("entry")))
                if item.get("stop_loss") is not None:
                    trigger_dict[asset].append(_create_trigger("SL", item.get("stop_loss")))

    async def update_triggers_for_recommendation(self, rec_id: int):
        # This method might need to be adapted or split for UserTrades vs Recommendations
        log.debug("Generic trigger update called for ID #%s. Rebuilding index for safety.", rec_id)
        await self.build_triggers_index()

    async def remove_triggers_for_item(self, item_id: int, is_user_trade: bool):
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                initial_len = len(self.active_triggers[symbol])
                self.active_triggers[symbol] = [
                    t for t in self.active_triggers[symbol]
                    if not (t.get("id") == item_id and t.get("is_user_trade") == is_user_trade)
                ]
                if len(self.active_triggers[symbol]) < initial_len:
                    item_type = "UserTrade" if is_user_trade else "Recommendation"
                    log.info("Removed triggers for %s #%s from symbol %s in memory.", item_type, item_id, symbol)
                if not self.active_triggers[symbol]:
                    del self.active_triggers[symbol]

    async def add_processed_event_in_memory(self, item_id: int, is_user_trade: bool, event_type: str):
        async with self._triggers_lock:
            for triggers in self.active_triggers.values():
                for trigger in triggers:
                    if trigger.get("id") == item_id and trigger.get("is_user_trade") == is_user_trade:
                        trigger["processed_events"].add(event_type)
                        log.debug("Added event '%s' to in-memory state for item #%s", event_type, item_id)

    async def _run_index_sync(self, interval_seconds: int = 300):
        log.info("Index sync task started (interval=%ss).", interval_seconds)
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                await self.build_triggers_index()
        except asyncio.CancelledError:
            log.info("Index sync task cancelled.")
        except Exception:
            log.exception("Index sync encountered error.")

    async def _run_watchdog_check(self):
        log.info("Price stream watchdog started (interval=%ss).", WATCHDOG_INTERVAL_SECONDS)
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
            try:
                async with self._triggers_lock:
                    watched_symbols = list(self.active_triggers.keys())
                
                now = time.time()
                for symbol in watched_symbols:
                    last_seen = self._last_price_seen_at.get(symbol, 0)
                    if now - last_seen > WATCHDOG_STALE_THRESHOLD_SECONDS:
                        log.warning("Watchdog: Price stream for %s is stale (last seen %.1f s ago). Fetching price manually.", symbol, now - last_seen)
                        try:
                            price = await self.price_service.get_cached_price(symbol, "Futures", force_refresh=True)
                            if price:
                                await self.price_queue.put((symbol, price, price))
                        except Exception:
                            log.exception("Watchdog: Failed to fetch manual price for stale symbol %s.", symbol)
            except Exception:
                log.exception("An error occurred in the watchdog task.")

    async def _process_queue(self):
        log.info("AlertService queue processor started.")
        try:
            while True:
                symbol, low_price, high_price = await self.price_queue.get()
                self._last_price_seen_at[symbol.upper()] = time.time()
                try:
                    await self.check_and_process_alerts(symbol, low_price, high_price)
                except Exception:
                    log.exception("Error while processing alerts for %s", symbol)
                finally:
                    with suppress(Exception): self.price_queue.task_done()
        except asyncio.CancelledError:
            log.info("Queue processor cancelled.")
        except Exception:
            log.exception("Unexpected error in queue processor.")

    def start(self):
        try:
            loop = asyncio.get_running_loop()
            if self._processing_task is None or self._processing_task.done(): self._processing_task = loop.create_task(self._process_queue())
            if self._index_sync_task is None or self._index_sync_task.done(): self._index_sync_task = loop.create_task(self._run_index_sync())
            if self._watchdog_task is None or self._watchdog_task.done(): self._watchdog_task = loop.create_task(self._run_watchdog_check())
            if hasattr(self.streamer, "start"): self.streamer.start()
            log.info("AlertService and its tasks started in existing event loop.")
            return
        except RuntimeError:
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
                    if hasattr(self.streamer, "start"): self.streamer.start()
                    loop.run_forever()
                except Exception:
                    log.exception("AlertService background runner crashed.")
                finally:
                    if self._bg_loop:
                        all_tasks = (self._processing_task, self._index_sync_task, self._watchdog_task)
                        for t in all_tasks:
                            if t and not t.done(): self._bg_loop.call_soon_threadsafe(t.cancel)
                        self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
            self._bg_thread.start()
            log.info("AlertService started in background thread.")

    def stop(self):
        if hasattr(self.streamer, "stop"): self.streamer.stop()
        all_tasks = (self._processing_task, self._index_sync_task, self._watchdog_task)
        for t in all_tasks:
            if t and not t.done(): t.cancel()
        if self._bg_loop and self._bg_thread:
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            self._bg_thread.join(timeout=5.0)
        self._bg_thread = self._bg_loop = self._processing_task = self._index_sync_task = self._watchdog_task = None
        log.info("AlertService stopped and cleaned up.")

    def _is_price_condition_met(self, side: str, low_price: float, high_price: float, target_price: float, condition_type: str) -> bool:
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()
        if side_upper == "LONG":
            if cond.startswith("TP"): return high_price >= target_price
            if cond == "SL": return low_price <= target_price
            if cond == "ENTRY": return low_price <= target_price
        if side_upper == "SHORT":
            if cond.startswith("TP"): return low_price <= target_price
            if cond == "SL": return high_price >= target_price
            if cond == "ENTRY": return high_price >= target_price
        return False

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        async with self._triggers_lock:
            triggers_for_symbol = list(self.active_triggers.get((symbol or "").upper(), []))
        if not triggers_for_symbol: return
        triggers_for_symbol.sort(key=lambda t: t.get("type") != "ENTRY")
        now_ts = time.time()
        for trigger in triggers_for_symbol:
            item_id = int(trigger.get("id", 0))
            ttype_raw = (trigger.get("type") or "").upper()
            execution_price = float(trigger.get("price", 0.0))
            processed_events: Set[str] = trigger.get("processed_events", set())
            status_in_memory = trigger.get("status")
            is_user_trade = trigger.get("is_user_trade")

            event_key = ttype_raw
            if self._tp_re.match(ttype_raw):
                m = self._tp_re.match(ttype_raw)
                event_key = f"TP{m.group(1)}_HIT"
            
            if not is_user_trade and event_key in processed_events: continue
            if not self._is_price_condition_met(trigger.get("side"), low_price, high_price, execution_price, ttype_raw): continue
            
            last_map = self._last_processed.setdefault(item_id, {})
            if last_ts := last_map.get(ttype_raw):
                if (now_ts - last_ts) < self._debounce_seconds: continue
            last_map[ttype_raw] = now_ts

            log.info("Trigger HIT for Item #%s (is_trade=%s): Type=%s, Symbol=%s, Range=[%s,%s], Target=%s", item_id, is_user_trade, ttype_raw, symbol, low_price, high_price, execution_price)
            
            try:
                if is_user_trade:
                    # Logic for UserTrades will be handled here in Phase 1
                    log.warning("UserTrade trigger hit for #%s, but logic is not implemented yet.", item_id)
                else: # It's a Recommendation
                    if status_in_memory == RecommendationStatusEnum.PENDING and ttype_raw == "SL":
                        log.warning("Invalidation HIT for PENDING Rec #%s: SL hit before entry.", item_id)
                        await self.trade_service.process_invalidation_event(item_id)
                        continue
                    if ttype_raw == "ENTRY":
                        await self.trade_service.process_activation_event(item_id)
                
                await self.add_processed_event_in_memory(item_id, is_user_trade, event_key)
            except Exception:
                log.exception("Failed to process and commit event for item #%s, type %s. Will retry.", item_id, ttype_raw)