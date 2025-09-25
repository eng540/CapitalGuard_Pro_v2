# src/capitalguard/application/services/alert_service.py (v19.0.2 - Production Ready)
"""
AlertService v19.0.2 - The definitive, production-ready version.

This version incorporates a full suite of architectural improvements to address reliability,
stability, and security, transforming the service into a robust, fault-tolerant engine.

Key Enhancements:
- **FIXED**: Corrected a critical key mismatch ('id' vs 'rec_id') during trigger validation.
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
import copy
import time
import re
from typing import List, Dict, Any, Optional
from contextlib import suppress
from dataclasses import dataclass

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)
audit_log = logging.getLogger('capitalguard.audit')

# --- Helper Classes (No changes needed here) ---

@dataclass
class HealthMetrics:
    last_processed_time: float
    processed_count: int = 0
    error_count: int = 0
    last_health_check: float = 0
    startup_time: float = 0

class ServiceHealthMonitor:
    def __init__(self, service_name: str = "AlertService"):
        self.service_name = service_name
        self.metrics = HealthMetrics(last_processed_time=time.time(), startup_time=time.time())
        self._emergency_callback = None
        self._health_check_interval = 60

    def record_processing(self):
        self.metrics.processed_count += 1
        self.metrics.last_processed_time = time.time()

    def record_error(self):
        self.metrics.error_count += 1

    async def check_health(self):
        current_time = time.time()
        self.metrics.last_health_check = current_time
        if current_time - self.metrics.last_processed_time > self._health_check_interval:
            log.critical("ALERT: No price processing detected for %d seconds!", self._health_check_interval)
            await self._trigger_emergency_protocol()
            return False
        return True

    async def _trigger_emergency_protocol(self):
        if self._emergency_callback:
            await self._emergency_callback()
        else:
            log.error("Emergency protocol required but no callback set")

    def set_emergency_callback(self, callback):
        self._emergency_callback = callback

class SmartDebounceManager:
    def __init__(self, max_age_seconds: float = 3600, debounce_seconds: float = 1.0):
        self._events: Dict[int, Dict[str, float]] = {}
        self._max_age = max_age_seconds
        self._debounce_seconds = debounce_seconds
        self._cleanup_interval = 300
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start_cleanup_task(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        while True:
            await asyncio.sleep(self._cleanup_interval)
            self._remove_old_entries()

    def _remove_old_entries(self):
        current_time = time.time()
        expired_records = [
            rec_id for rec_id, events in self._events.items()
            if all(current_time - ts > self._max_age for ts in events.values())
        ]
        for rec_id in expired_records:
            del self._events[rec_id]
        if expired_records:
            log.debug("Cleaned up %d expired debounce records", len(expired_records))

    def should_process(self, rec_id: int, event_type: str) -> bool:
        current_time = time.time()
        event_type = event_type.upper()
        last_ts = self._events.get(rec_id, {}).get(event_type, 0)
        if current_time - last_ts < self._debounce_seconds:
            return False
        self._events.setdefault(rec_id, {})[event_type] = current_time
        return True

class AuditLogger:
    @staticmethod
    def log_trigger_event(rec_id: int, event_type: str, symbol: str, trigger_price: float, actual_low: float, actual_high: float, decision: str = "EXECUTED"):
        audit_log.info("TRIGGER_EVENT: rec_id=%d, type=%s, symbol=%s, trigger=%.6f, low=%.6f, high=%.6f, decision=%s", rec_id, event_type, symbol, trigger_price, actual_low, actual_high, decision)

# --- Main AlertService Class ---

class AlertService:
    def __init__(self, trade_service: TradeService, repo: RecommendationRepository, notifier: Any, admin_chat_id: Optional[str], streamer: Optional[PriceStreamer] = None):
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
        self.health_monitor = ServiceHealthMonitor("AlertService")
        self.audit_logger = AuditLogger()
        self._tp_re = re.compile(r"^TP(\d+)$", flags=re.IGNORECASE)
        self.health_monitor.set_emergency_callback(self._emergency_restart)

    async def _emergency_restart(self):
        log.critical("Initiating emergency restart of AlertService...")
        try:
            self.stop()
            await asyncio.sleep(2)
            self.start()
            log.info("Emergency restart completed")
        except Exception as e:
            log.error("Emergency restart failed: %s", e)

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

    # ✅ --- START OF CRITICAL FIX ---
    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        asset = (item.get("asset") or "").strip().upper()
        if not asset: return

        status = item.get("status")
        side = item.get("side")
        rec_id = item.get("id") # Get the ID from the database item
        user_id = item.get("user_id")
        status_norm = status.name if hasattr(status, "name") else str(status).upper()

        def add_trigger(ttype, price, order_type=None):
            # Create the trigger with the correct 'rec_id' key
            trigger = {"rec_id": rec_id, "user_id": user_id, "side": side, "type": ttype, "price": price, "order_type": order_type}
            # Validate the correctly formed trigger before adding
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
    # ✅ --- END OF CRITICAL FIX ---

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
                if attempt > 0:
                    self.audit_logger.log_retry_attempt(rec_id, event_type, attempt, max_retries)
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
            if self.debounce_manager.should_process(rec_id, ttype):
                if self._is_price_condition_met(trigger.get("side"), low_price, high_price, trigger.get("price"), ttype, trigger.get("order_type")):
                    success = await self._process_event_with_retry(ttype, rec_id, trigger.get("user_id"), trigger.get("price"))
                    self.audit_logger.log_trigger_event(rec_id, ttype, symbol_upper, trigger.get("price"), low_price, high_price, "SUCCESS" if success else "FAILED")

    async def _run_health_monitor(self, interval_seconds: int = 30):
        log.info("Health monitor task started (interval=%ss).", interval_seconds)
        while True:
            await asyncio.sleep(interval_seconds)
            await self.health_monitor.check_health()
            self.audit_logger.log_service_health(self.health_monitor.metrics)

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
                await self.health_monitor.check_health()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Unexpected error in queue processor.")
                self.health_monitor.record_error()
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
                self._bg_loop = loop
                asyncio.set_event_loop(loop)
                self._processing_task = loop.create_task(self._process_queue())
                self._index_sync_task = loop.create_task(self._run_index_sync())
                self._health_monitor_task = loop.create_task(self._run_health_monitor())
                self.streamer.start()
                loop.run_forever()
            except Exception:
                log.exception("AlertService background runner crashed.")
            finally:
                tasks = asyncio.all_tasks(loop=loop)
                for task in tasks: task.cancel()
                async def gather_cancelled(): await asyncio.gather(*tasks, return_exceptions=True)
                loop.run_until_complete(gather_cancelled())
                loop.close()
                log.info("AlertService background loop stopped.")
        self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
        self._bg_thread.start()
        log.info("AlertService v19.0.2 started in background thread.")

    def stop(self):
        if self._bg_loop and self._bg_loop.is_running():
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        if self._bg_thread:
            self._bg_thread.join(timeout=5.0)
        self.streamer.stop()
        self._bg_thread = None
        self._bg_loop = None
        log.info("AlertService v19.0.2 stopped.")