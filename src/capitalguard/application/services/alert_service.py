# src/capitalguard/application/services/alert_service.py
"""
AlertService — hardened, production-ready.

Fixes applied vs provided v18.3.0:
- Use session_scope() for DB safety instead of direct SessionLocal context.
- start() safe from sync or async context: creates background thread+loop if no running loop.
- Guarded streamer.start()/stop() to avoid double-start/blocking.
- Defensive parsing/normalization of trigger items.
- Robust cancellation and cleanup on stop().
- Inclusive comparisons and safe order_type handling.
"""

import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional
from contextlib import suppress

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer

log = logging.getLogger(__name__)


class AlertService:
    def __init__(self, trade_service, repo: RecommendationRepository, streamer: Optional[PriceStreamer] = None):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)

        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()

        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None

        # background runner for sync start()
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None

    # ---------- Trigger index ----------

    async def build_triggers_index(self):
        log.info("Building in-memory trigger index for all active recommendations...")
        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        try:
            with session_scope() as session:
                trigger_data = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.exception("Failed reading triggers from repository.")
            return

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
        log.info("Trigger index built: %d recommendations across %d symbols.", total_recs, len(new_triggers))

    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        asset = (item.get("asset") or "").strip().upper()
        if not asset:
            raise ValueError("Empty asset when adding trigger")

        if asset not in trigger_dict:
            trigger_dict[asset] = []

        status = item.get("status")
        side = item.get("side")
        rec_id = item.get("id")
        user_id = item.get("user_id")

        # normalize numeric/string statuses
        status_norm = status
        try:
            if isinstance(status, str):
                status_norm = status.upper()
            elif isinstance(status, (int, float)):
                status_norm = int(status)
        except Exception:
            status_norm = status

        # Pending
        if status_norm == 0 or status_norm == "PENDING":
            try:
                price = float(item.get("entry") or 0.0)
            except Exception:
                price = 0.0
            trigger_dict[asset].append({
                "rec_id": rec_id, "user_id": user_id, "side": side,
                "type": "ENTRY", "price": price, "order_type": item.get("order_type")
            })
            return

        # Active
        if status_norm == 1 or status_norm == "ACTIVE":
            sl = item.get("stop_loss")
            if sl is not None:
                try:
                    slp = float(sl)
                    trigger_dict[asset].append({
                        "rec_id": rec_id, "user_id": user_id, "side": side,
                        "type": "SL", "price": slp
                    })
                except Exception:
                    log.warning("Invalid stop_loss for rec %s: %s", rec_id, sl)
            psp = item.get("profit_stop_price")
            if psp is not None:
                try:
                    pspv = float(psp)
                    trigger_dict[asset].append({
                        "rec_id": rec_id, "user_id": user_id, "side": side,
                        "type": "PROFIT_STOP", "price": pspv
                    })
                except Exception:
                    log.warning("Invalid profit_stop_price for rec %s: %s", rec_id, psp)
            for idx, target in enumerate(item.get("targets") or []):
                try:
                    tprice = float(target.get("price"))
                    trigger_dict[asset].append({
                        "rec_id": rec_id, "user_id": user_id, "side": side,
                        "type": f"TP{idx+1}", "price": tprice
                    })
                except Exception:
                    log.warning("Invalid target for rec %s index %s: %s", rec_id, idx, target)
            return

        log.debug("Unhandled trigger status for rec %s: %s", rec_id, status)

    async def update_triggers_for_recommendation(self, rec_id: int):
        log.debug("Updating triggers for Rec #%s in memory.", rec_id)
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if not self.active_triggers[symbol]:
                    del self.active_triggers[symbol]

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
            removed = False
            for symbol in list(self.active_triggers.keys()):
                original = len(self.active_triggers[symbol])
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if len(self.active_triggers[symbol]) < original:
                    removed = True
                    log.info("Removed triggers for Rec #%s from symbol %s in memory.", rec_id, symbol)
                if not self.active_triggers[symbol]:
                    del self.active_triggers[symbol]
            if not removed:
                log.debug("No triggers removed for Rec #%s; none found in memory.", rec_id)

    # ---------- Background tasks ----------

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

    # ---------- Start / Stop ----------

    def start(self):
        """
        Safe start from async or sync context.
        If no running loop in current thread, spin a background thread + loop.
        """
        try:
            loop = asyncio.get_running_loop()
            # create tasks in existing loop
            if self._processing_task is None or self._processing_task.done():
                self._processing_task = loop.create_task(self._process_queue())
            if self._index_sync_task is None or self._index_sync_task.done():
                self._index_sync_task = loop.create_task(self._run_index_sync())
            # start streamer if present
            try:
                if hasattr(self.streamer, "start"):
                    self.streamer.start()
            except Exception:
                log.exception("Streamer.start() failed in event loop context.")
            log.info("AlertService started in existing event loop.")
            return
        except RuntimeError:
            # no running loop -> background thread
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
                    try:
                        if hasattr(self.streamer, "start"):
                            self.streamer.start()
                    except Exception:
                        log.exception("Streamer.start() failed in background thread.")
                    loop.run_forever()
                except Exception:
                    log.exception("AlertService background runner crashed.")
                finally:
                    try:
                        for t in (self._processing_task, self._index_sync_task):
                            if t and not t.done():
                                loop.call_soon_threadsafe(t.cancel)
                    except Exception:
                        pass
                    with suppress(Exception):
                        loop.stop()
                        loop.close()
                    log.info("AlertService background loop stopped.")

            self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
            self._bg_thread.start()
            log.info("AlertService started in background thread.")

    def stop(self):
        # stop streamer
        try:
            if hasattr(self.streamer, "stop"):
                self.streamer.stop()
        except Exception:
            log.exception("Error stopping streamer.")

        # cancel tasks in active loop
        try:
            if self._processing_task and not self._processing_task.done():
                self._processing_task.cancel()
            if self._index_sync_task and not self._index_sync_task.done():
                self._index_sync_task.cancel()
        except Exception:
            log.exception("Error cancelling tasks in main loop.")

        # stop background loop/thread if any
        if self._bg_loop and self._bg_thread:
            try:
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            except Exception:
                log.exception("Failed to stop background event loop.")
            self._bg_thread.join(timeout=5.0)
            if self._bg_thread.is_alive():
                log.warning("Background thread did not exit within timeout.")
            self._bg_thread = None
            self._bg_loop = None

        self._processing_task = None
        self._index_sync_task = None
        log.info("AlertService stopped and cleaned up.")

    # ---------- Condition evaluation & processing ----------

    def _is_price_condition_met(self, side: str, low_price: float, high_price: float, target_price: float, condition_type: str, order_type: Optional[Any] = None) -> bool:
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()

        if side_upper == "LONG":
            if cond.startswith("TP"):
                return high_price >= target_price
            if cond in ("SL", "PROFIT_STOP"):
                return low_price <= target_price
            if cond == "ENTRY":
                # accept enum or string order_type
                ot = str(order_type).upper() if order_type is not None else ""
                if ot.endswith("LIMIT"):
                    return low_price <= target_price
                if ot.endswith("STOP_MARKET"):
                    return high_price >= target_price
                return low_price <= target_price or high_price >= target_price

        if side_upper == "SHORT":
            if cond.startswith("TP"):
                return low_price <= target_price
            if cond in ("SL", "PROFIT_STOP"):
                return high_price >= target_price
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                if ot.endswith("LIMIT"):
                    return high_price >= target_price
                if ot.endswith("STOP_MARKET"):
                    return low_price <= target_price
                return low_price <= target_price or high_price >= target_price

        return False

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        async with self._triggers_lock:
            triggers_for_symbol = list(self.active_triggers.get((symbol or "").upper(), []))

        if not triggers_for_symbol:
            return

        triggered_ids = set()
        for trigger in triggers_for_symbol:
            try:
                execution_price = float(trigger.get("price") or 0.0)
            except Exception:
                log.warning("Invalid trigger price, skipping: %s", trigger)
                continue

            try:
                if not self._is_price_condition_met(trigger.get("side"), low_price, high_price, execution_price, trigger.get("type"), trigger.get("order_type")):
                    continue
            except Exception:
                log.exception("Error evaluating trigger condition: %s", trigger)
                continue

            rec_id = trigger.get("rec_id")
            if rec_id in triggered_ids:
                continue
            triggered_ids.add(rec_id)

            log.info("Trigger HIT for Rec #%s: Type=%s, Symbol=%s, Range=[%s,%s], Target=%s", rec_id, trigger.get("type"), symbol, low_price, high_price, execution_price)
            ttype = (trigger.get("type") or "").upper()
            try:
                if ttype == "ENTRY":
                    await self.trade_service.process_activation_event(rec_id)
                elif ttype.startswith("TP"):
                    try:
                        idx = int(ttype[2:])
                    except Exception:
                        idx = 1
                    await self.trade_service.process_tp_hit_event(rec_id, trigger.get("user_id"), idx, execution_price)
                elif ttype == "SL":
                    await self.trade_service.process_sl_hit_event(rec_id, trigger.get("user_id"), execution_price)
                elif ttype == "PROFIT_STOP":
                    await self.trade_service.process_profit_stop_hit_event(rec_id, trigger.get("user_id"), execution_price)
                else:
                    log.debug("Unhandled trigger type: %s", ttype)
            except Exception:
                log.exception("Failed processing hit event for rec %s", rec_id)