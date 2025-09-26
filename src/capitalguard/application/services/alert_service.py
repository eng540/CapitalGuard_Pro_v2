# src/capitalguard/application/services/alert_service.py v19.1.1 (Syntax Hotfix)
"""
AlertService — Final version with transaction-aware state updates and robust logic.

Key features:
- HOTFIX: Corrected a SyntaxError that prevented the application from starting.
- Stateful event awareness: Fetches and stores processed events to prevent duplicate triggers.
- Transaction-aware state updates: In-memory state is updated ONLY AFTER the
  TradeService's database transaction is successfully committed.
- Robust "level crossing" algorithm for price condition checks, handling gaps and volatility.
- Self-healing: Periodically rebuilds the entire index from the database.
"""

import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional, Set
from contextlib import suppress
import time
import re

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer

log = logging.getLogger(__name__)


class AlertService:
    def __init__(self, trade_service, repo: RecommendationRepository, streamer: Optional[PriceStreamer] = None, debounce_seconds: float = 1.0):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)

        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()

        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None

        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None

        self._last_processed: Dict[int, Dict[str, float]] = {}
        self._debounce_seconds = float(debounce_seconds)

        self._tp_re = re.compile(r"^TP(\d+)$", flags=re.IGNORECASE)

    # ---------- Trigger index ----------

    async def build_triggers_index(self):
        log.info("Attempting to build in-memory trigger index for all active recommendations...")
        retry_delay = 5
        while True:
            try:
                with session_scope() as session:
                    trigger_data = self.repo.list_all_active_triggers_data(session)
                break
            except Exception:
                log.critical("CRITICAL: Failed to read triggers from repository. Retrying in %ds...", retry_delay, exc_info=True)
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

        total_recs = len(trigger_data) if trigger_data is not None else 0
        log.info("✅ Trigger index built successfully: %d recommendations across %d symbols.", total_recs, len(new_triggers))

    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        asset = (item.get("asset") or "").strip().upper()
        if not asset: raise ValueError("Empty asset")
        if asset not in trigger_dict: trigger_dict[asset] = []

        rec_id = item.get("id")
        processed_events = item.get("processed_events", set())

        def _create_trigger(trigger_type: str, price: Any) -> Dict[str, Any]:
            return {
                "rec_id": rec_id, "user_id": item.get("user_id"), "side": item.get("side"),
                "type": trigger_type, "price": float(price), "order_type": item.get("order_type"),
                "processed_events": processed_events
            }

        status = item.get("status")
        status_norm = status.name if hasattr(status, "name") else str(status).upper()

        if status_norm == "PENDING":
            trigger_dict[asset].append(_create_trigger("ENTRY", item.get("entry")))
            return

        if status_norm == "ACTIVE":
            if item.get("stop_loss") is not None:
                trigger_dict[asset].append(_create_trigger("SL", item.get("stop_loss")))
            if item.get("profit_stop_price") is not None:
                trigger_dict[asset].append(_create_trigger("PROFIT_STOP", item.get("profit_stop_price")))
            for idx, target in enumerate(item.get("targets", []), 1):
                trigger_dict[asset].append(_create_trigger(f"TP{idx}", target.get("price")))

    async def update_triggers_for_recommendation(self, rec_id: int):
        log.debug("Updating triggers for Rec #%s in memory.", rec_id)
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if not self.active_triggers[symbol]: del self.active_triggers[symbol]

        try:
            with session_scope() as session:
                item = self.repo.get_active_trigger_data_by_id(session, rec_id)
        except Exception:
            log.exception("Failed fetching active trigger data for rec %s", rec_id)
            return

        if not item:
            log.debug("No active trigger found for rec %s.", rec_id)
            return

        asset = (item.get("asset") or "").strip().upper()
        item["asset"] = asset
        async with self._triggers_lock:
            try:
                self._add_item_to_trigger_dict(self.active_triggers, item)
                log.info("Updated triggers for Rec #%s in memory under symbol %s.", rec_id, asset)
            except Exception:
                log.exception("Failed to add updated trigger for rec %s", rec_id)

    async def remove_triggers_for_recommendation(self, rec_id: int):
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                before_len = len(self.active_triggers[symbol])
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if len(self.active_triggers[symbol]) < before_len:
                    log.info("Removed triggers for Rec #%s from symbol %s in memory.", rec_id, symbol)
                if not self.active_triggers[symbol]: del self.active_triggers[symbol]

    async def add_processed_event_in_memory(self, rec_id: int, event_type: str):
        """Immediately updates the in-memory state to prevent duplicate events."""
        async with self._triggers_lock:
            for symbol, triggers in self.active_triggers.items():
                for trigger in triggers:
                    if trigger.get("rec_id") == rec_id:
                        trigger["processed_events"].add(event_type)
                        log.debug("Added event '%s' to in-memory state for Rec #%s", event_type, rec_id)

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

    async def _process_queue(self):
        log.info("AlertService queue processor started.")
        try:
            while True:
                symbol, low_price, high_price = await self.price_queue.get()
                try:
                    await self.check_and_process_alerts(symbol, low_price, high_price)
                except Exception:
                    log.exception("Error while processing alerts for %s", symbol)
                finally:
                    with suppress(Exception):
                        self.price_queue.task_done()
        except asyncio.CancelledError:
            log.info("Queue processor cancelled.")
        except Exception:
            log.exception("Unexpected error in queue processor.")

    def start(self):
        try:
            loop = asyncio.get_running_loop()
            if self._processing_task is None or self._processing_task.done():
                self._processing_task = loop.create_task(self._process_queue())
            if self._index_sync_task is None or self._index_sync_task.done():
                self._index_sync_task = loop.create_task(self._run_index_sync())
            if hasattr(self.streamer, "start"): self.streamer.start()
            log.info("AlertService started in existing event loop.")
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
                    if hasattr(self.streamer, "start"): self.streamer.start()
                    loop.run_forever()
                except Exception:
                    log.exception("AlertService background runner crashed.")
                finally:
                    if self._bg_loop:
                        for t in (self._processing_task, self._index_sync_task):
                            if t and not t.done(): self._bg_loop.call_soon_threadsafe(t.cancel)
                        self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            
            self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
            self._bg_thread.start()
            log.info("AlertService started in background thread.")

    def stop(self):
        if hasattr(self.streamer, "stop"): self.streamer.stop()
        if self._processing_task and not self._processing_task.done(): self._processing_task.cancel()
        if self._index_sync_task and not self._index_sync_task.done(): self._index_sync_task.cancel()
        if self._bg_loop and self._bg_thread:
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            self._bg_thread.join(timeout=5.0)
        self._bg_thread = self._bg_loop = self._processing_task = self._index_sync_task = None
        log.info("AlertService stopped and cleaned up.")

    def _is_price_condition_met(self, side: str, low_price: float, high_price: float, target_price: float, condition_type: str, order_type: Optional[Any] = None) -> bool:
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()
        if side_upper == "LONG":
            if cond.startswith("TP"): return high_price >= target_price
            if cond in ("SL", "PROFIT_STOP"): return low_price <= target_price
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                if ot.endswith("LIMIT"): return low_price <= target_price
                if ot.endswith("STOP_MARKET"): return high_price >= target_price
                return low_price <= target_price or high_price >= target_price
        if side_upper == "SHORT":
            if cond.startswith("TP"): return low_price <= target_price
            if cond in ("SL", "PROFIT_STOP"): return high_price >= target_price
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                if ot.endswith("LIMIT"): return high_price >= target_price
                if ot.endswith("STOP_MARKET"): return low_price <= target_price
                return low_price <= target_price or high_price >= target_price
        return False

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        async with self._triggers_lock:
            triggers_for_symbol = list(self.active_triggers.get((symbol or "").upper(), []))
        if not triggers_for_symbol: return

        now_ts = time.time()
        for trigger in triggers_for_symbol:
            rec_id = int(trigger.get("rec_id", 0))
            ttype_raw = (trigger.get("type") or "").upper()
            execution_price = float(trigger.get("price", 0.0))
            processed_events: Set[str] = trigger.get("processed_events", set())

            event_key = ttype_raw
            if self._tp_re.match(ttype_raw):
                m = self._tp_re.match(ttype_raw)
                event_key = f"TP{m.group(1)}_HIT"
            
            if event_key in processed_events:
                continue

            if not self._is_price_condition_met(trigger.get("side"), low_price, high_price, execution_price, ttype_raw, trigger.get("order_type")):
                continue

            last_map = self._last_processed.setdefault(rec_id, {})
            if last_ts := last_map.get(ttype_raw):
                if (now_ts - last_ts) < self._debounce_seconds:
                    continue
            last_map[ttype_raw] = now_ts

            log.info("Trigger HIT for Rec #%s: Type=%s, Symbol=%s, Range=[%s,%s], Target=%s", rec_id, ttype_raw, symbol, low_price, high_price, execution_price)
            
            try:
                if ttype_raw == "ENTRY":
                    await self.trade_service.process_activation_event(rec_id)
                elif self._tp_re.match(ttype_raw):
                    m = self._tp_re.match(ttype_raw)
                    idx = int(m.group(1)) if m else 1
                    await self.trade_service.process_tp_hit_event(rec_id, trigger.get("user_id"), idx, execution_price)
                elif ttype_raw == "SL":
                    await self.trade_service.process_sl_hit_event(rec_id, trigger.get("user_id"), execution_price)
                elif ttype_raw == "PROFIT_STOP":
                    await self.trade_service.process_profit_stop_hit_event(rec_id, trigger.get("user_id"), execution_price)
                
                await self.add_processed_event_in_memory(rec_id, event_key)

            except Exception:
                # ✅ SYNTAX FIX: The erroneous backticks have been removed from this line.
                log.exception("Failed to process and commit event for rec #%s, type %s. Will retry.", rec_id, ttype_raw)