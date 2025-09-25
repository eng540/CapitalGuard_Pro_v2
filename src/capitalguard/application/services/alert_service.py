# src/capitalguard/application/services/alert_service.py (v19.0.5 - Production Ready)
"""
AlertService v19.0.5 - The definitive, production-ready version.

This version incorporates a full suite of architectural improvements to address reliability,
stability, and security, transforming the service into a robust, fault-tolerant engine.

Key Enhancements:
- **FIXED**: Implemented thread-safe notifier calls from the health monitor to resolve TypeError.
- **FIXED**: Resolved a critical `RuntimeError: no running event loop` by correctly setting the event loop within the background thread before starting sub-tasks.
- **FIXED**: Corrected a key mismatch ('id' vs 'rec_id') during trigger validation.
- **Health Monitoring:** Actively monitors the price queue for stale data and triggers alerts.
- **Memory Leak Fix:** Implements a smart debounce manager with automatic cleanup.
- **Intelligent Retries:** Failed event processing is automatically retried with exponential backoff.
- **Concurrency Safety:** Uses deep copies of trigger data to prevent race conditions.
- **Data Validation:** Incoming trigger data is validated before being added to the index.
- **Atomic Updates:** Trigger index updates are now atomic, eliminating synchronization gaps.
- **Precision Price Logic:** Price condition checks include a safety margin.
- **Audit Trail:** Logs every critical decision for full traceability.
- **Resilient Queue Processing:** The price queue now has a timeout to prevent silent stalls.
"""

import logging
import asyncio
import threading
import time
import re
import copy
from typing import List, Dict, Any, Optional
from contextlib import suppress

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)
audit_log = logging.getLogger('capitalguard.audit')

class ServiceHealthMonitor:
    """Monitors the health of the AlertService processing loop."""
    def __init__(self, notifier: Any, admin_chat_id: Optional[str], main_loop: asyncio.AbstractEventLoop, stale_threshold_sec: int = 90):
        self.last_processed_time = time.time()
        self.stale_threshold = stale_threshold_sec
        self.notifier = notifier
        self.admin_chat_id = admin_chat_id
        self.main_loop = main_loop
        self.alert_sent = False

    def record_processing(self):
        """Records a successful processing event."""
        self.last_processed_time = time.time()
        self.alert_sent = False

    def check_health(self):
        """Checks if the service is stale and sends a critical alert in a thread-safe manner."""
        if time.time() - self.last_processed_time > self.stale_threshold:
            if not self.alert_sent and self.admin_chat_id and self.notifier and self.main_loop:
                log.critical("HEALTH ALERT: No price processing detected for %d seconds!", self.stale_threshold)
                try:
                    message = f"🚨 CRITICAL ALERT: Price watcher appears to be stalled. No prices processed for over {self.stale_threshold} seconds. Please investigate immediately."
                    # ✅ --- CRITICAL FIX: Schedule the notifier call on the main event loop ---
                    asyncio.run_coroutine_threadsafe(
                        self.notifier.send_private_text(chat_id=int(self.admin_chat_id), text=message),
                        self.main_loop
                    )
                    self.alert_sent = True
                except Exception:
                    log.exception("Failed to send critical health alert to admin.")

class SmartDebounceManager:
    """Manages event debouncing with automatic memory cleanup to prevent leaks."""
    def __init__(self, debounce_seconds: float = 1.0, max_age_seconds: float = 3600.0):
        self._events: Dict[int, Dict[str, float]] = {}
        self._debounce_seconds = debounce_seconds
        self._max_age = max_age_seconds
        self._cleanup_interval = 600
        self._cleanup_task: Optional[asyncio.Task] = None

    def start_cleanup_task(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            log.info("SmartDebounceManager cleanup task started.")

    async def _periodic_cleanup(self):
        while True:
            await asyncio.sleep(self._cleanup_interval)
            now = time.time()
            cleaned_count = 0
            for rec_id, events in list(self._events.items()):
                for event_type, timestamp in list(events.items()):
                    if now - timestamp > self._max_age:
                        del self._events[rec_id][event_type]
                        cleaned_count += 1
                if not self._events[rec_id]:
                    del self._events[rec_id]
            if cleaned_count > 0:
                log.debug("DebounceManager cleaned up %d old entries.", cleaned_count)

    def is_debounced(self, rec_id: int, event_type: str) -> bool:
        now = time.time()
        last_map = self._events.setdefault(rec_id, {})
        last_ts = last_map.get(event_type)
        if last_ts and (now - last_ts) < self._debounce_seconds:
            return True
        last_map[event_type] = now
        return False

class AuditLogger:
    @staticmethod
    def log_trigger_event(rec_id: int, event_type: str, symbol: str, trigger_price: float, actual_low: float, actual_high: float, decision: str = "EXECUTED"):
        audit_log.info("TRIGGER_EVENT: rec_id=%d, type=%s, symbol=%s, trigger=%.6f, low=%.6f, high=%.6f, decision=%s", rec_id, event_type, symbol, trigger_price, actual_low, actual_high, decision)

class AlertService:
    def __init__(self, trade_service: TradeService, repo: RecommendationRepository, notifier: Any, admin_chat_id: Optional[str], main_loop: asyncio.AbstractEventLoop, streamer: Optional[PriceStreamer] = None):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)
        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()
        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._health_monitor_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self.debounce_manager = SmartDebounceManager(debounce_seconds=1.0)
        self.health_monitor = ServiceHealthMonitor(notifier, admin_chat_id, main_loop)
        self.audit_logger = AuditLogger()
        self._tp_re = re.compile(r"^TP(\d+)$", flags=re.IGNORECASE)

    def _validate_trigger_data(self, trigger: Dict[str, Any]) -> bool:
        required_fields = ['rec_id', 'type', 'price', 'side']
        for field in required_fields:
            if field not in trigger or trigger[field] is None:
                log.error("Validation failed: Missing required field '%s' in trigger: %s", field, trigger)
                return False
        try:
            price = float(trigger['price'])
            if not (price > 0):
                raise ValueError("Price must be positive")
        except (ValueError, TypeError):
            log.error("Validation failed: Non-numeric or invalid price in trigger: %s", trigger)
            return False
        return True

    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        asset = (item.get("asset") or "").strip().upper()
        if not asset: return
        status = item.get("status")
        side = item.get("side")
        rec_id = item.get("id")
        user_id = item.get("user_id")
        status_norm = status.name if hasattr(status, "name") else str(status).upper()
        def add_trigger(ttype, price, order_type=None):
            trigger = {"rec_id": rec_id, "user_id": user_id, "side": side, "type": ttype, "price": price, "order_type": order_type}
            if self._validate_trigger_data(trigger):
                trigger_dict.setdefault(asset, []).append(trigger)
        if status_norm in ("0", "PENDING"):
            add_trigger("ENTRY", item.get("entry"), item.get("order_type"))
        elif status_norm in ("1", "ACTIVE"):
            add_trigger("SL", item.get("stop_loss"))
            if item.get("profit_stop_price") is not None:
                add_trigger("PROFIT_STOP", item.get("profit_stop_price"))
            for idx, target in enumerate(item.get("targets") or []):
                add_trigger(f"TP{idx+1}", target.get("price"))

    async def build_triggers_index(self):
        log.info("Building enhanced in-memory trigger index for all active recommendations...")
        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        trigger_data = []
        try:
            with session_scope() as session:
                trigger_data = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.exception("Failed reading triggers from repository.")
            return
        processed_count = 0
        for item in trigger_data:
            try:
                self._add_item_to_trigger_dict(new_triggers, item)
                processed_count += 1
            except Exception:
                log.exception("Failed processing trigger item: %s", item)
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        log.info("Trigger index built: %d recommendations processed across %d symbols.", processed_count, len(new_triggers))

    async def update_triggers_for_recommendation(self, rec_id: int):
        log.debug("Updating triggers for Rec #%s with atomic update.", rec_id)
        item = None
        try:
            with session_scope() as session:
                item = self.repo.get_active_trigger_data_by_id(session, rec_id)
        except Exception:
            log.exception("Failed fetching active trigger data for rec %s", rec_id)
            return
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if not self.active_triggers[symbol]:
                    del self.active_triggers[symbol]
            if item:
                self._add_item_to_trigger_dict(self.active_triggers, item)
                log.info("Atomically updated triggers for Rec #%s.", rec_id)
            else:
                log.info("Atomically removed triggers for closed/invalid Rec #%s.", rec_id)

    async def remove_triggers_for_recommendation(self, rec_id: int):
        await self.update_triggers_for_recommendation(rec_id)

    async def _process_event_with_retry(self, event_type: str, rec_id: int, user_id: str, price: float, max_retries: int = 3) -> bool:
        for attempt in range(max_retries):
            try:
                if event_type == "ENTRY":
                    await self.trade_service.process_activation_event(rec_id)
                elif self._tp_re.match(event_type):
                    idx = int(self._tp_re.match(event_type).group(1))
                    await self.trade_service.process_tp_hit_event(rec_id, user_id, idx, price)
                elif event_type == "SL":
                    await self.trade_service.process_sl_hit_event(rec_id, user_id, price)
                elif event_type == "PROFIT_STOP":
                    await self.trade_service.process_profit_stop_hit_event(rec_id, user_id, price)
                else:
                    log.error("Unhandled event type: %s", event_type)
                    return False
                return True
            except Exception as e:
                log.error("Attempt %d/%d failed for %s event on rec %s: %s", attempt + 1, max_retries, event_type, rec_id, e)
                if attempt == max_retries - 1:
                    log.critical("Final attempt failed for event %s on rec #%d.", event_type, rec_id)
                else:
                    await asyncio.sleep(2 ** attempt)
        return False

    def _is_price_condition_met(self, side: str, low_price: float, high_price: float, target_price: float, condition_type: str, order_type: Optional[Any] = None) -> bool:
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()
        margin = target_price * 0.0001
        if side_upper == "LONG":
            if cond.startswith("TP"): return high_price >= target_price - margin
            if cond in ("SL", "PROFIT_STOP"): return low_price <= target_price + margin
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type else ""
                if "LIMIT" in ot: return low_price <= target_price + margin
                if "STOP" in ot: return high_price >= target_price - margin
                return low_price <= target_price + margin
        elif side_upper == "SHORT":
            if cond.startswith("TP"): return low_price <= target_price + margin
            if cond in ("SL", "PROFIT_STOP"): return high_price >= target_price - margin
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type else ""
                if "LIMIT" in ot: return high_price >= target_price - margin
                if "STOP" in ot: return low_price <= target_price + margin
                return high_price >= target_price - margin
        return False

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        symbol_upper = (symbol or "").upper()
        async with self._triggers_lock:
            triggers_for_symbol = copy.deepcopy(self.active_triggers.get(symbol_upper, []))
        if not triggers_for_symbol:
            return
        for trigger in triggers_for_symbol:
            rec_id = trigger.get("rec_id")
            ttype = trigger.get("type")
            if self.debounce_manager.is_debounced(rec_id, ttype):
                continue
            if self._is_price_condition_met(trigger.get("side"), low_price, high_price, trigger.get("price"), ttype, trigger.get("order_type")):
                success = await self._process_event_with_retry(ttype, rec_id, trigger.get("user_id"), trigger.get("price"))
                self.audit_logger.log_trigger_event(rec_id, ttype, symbol_upper, trigger.get("price"), low_price, high_price, "SUCCESS" if success else "FAILED")

    async def _run_health_monitor(self, interval_seconds: int = 30):
        log.info("Health monitor task started (interval=%ss).", interval_seconds)
        while True:
            await asyncio.sleep(interval_seconds)
            self.health_monitor.check_health()

    async def _run_index_sync(self, interval_seconds: int = 300):
        log.info("Index sync task started (interval=%ss).", interval_seconds)
        while True:
            await asyncio.sleep(interval_seconds)
            await self.build_triggers_index()

    async def _process_queue(self):
        log.info("AlertService queue processor started with enhanced reliability.")
        await self.debounce_manager.start_cleanup_task()
        while True:
            try:
                symbol, low_price, high_price = await asyncio.wait_for(self.price_queue.get(), timeout=90.0)
                self.health_monitor.record_processing()
                await self.check_and_process_alerts(symbol, low_price, high_price)
            except asyncio.TimeoutError:
                log.warning("Price queue timeout - checking service health...")
                self.health_monitor.check_health()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Unexpected error in queue processor.")
            finally:
                with suppress(Exception):
                    self.price_queue.task_done()

    def start(self):
        if self._bg_thread and self._bg_thread.is_alive():
            log.warning("AlertService background thread already running.")
            return
        def _bg_runner():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._bg_loop = loop
                async def startup():
                    self.streamer.start()
                    self._processing_task = asyncio.create_task(self._process_queue())
                    self._index_sync_task = asyncio.create_task(self._run_index_sync())
                    self._health_monitor_task = asyncio.create_task(self._run_health_monitor())
                    log.info("All AlertService tasks started successfully.")
                loop.run_until_complete(startup())
                log.info("AlertService background loop starting...")
                loop.run_forever()
            except Exception:
                log.exception("AlertService background runner crashed.")
            finally:
                if self._bg_loop and self._bg_loop.is_running():
                    tasks = asyncio.all_tasks(loop=self._bg_loop)
                    for task in tasks: task.cancel()
                    async def gather_cancelled(): await asyncio.gather(*tasks, return_exceptions=True)
                    self._bg_loop.run_until_complete(gather_cancelled())
                    self._bg_loop.close()
                log.info("AlertService background loop stopped.")
        self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
        self._bg_thread.start()
        log.info("AlertService v19.0.5 started in background thread.")

    def stop(self):
        if self._bg_loop and self._bg_loop.is_running():
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        if self._bg_thread:
            self._bg_thread.join(timeout=5.0)
        self.streamer.stop()
        self._bg_thread = None
        self._bg_loop = None
        log.info("AlertService v19.0.5 stopped.")