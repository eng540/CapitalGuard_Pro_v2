# src/capitalguard/application/services/alert_service.py v18.5.0 (Final Logic)
"""
AlertService — Final, robust logic for trigger conditions.

Key fixes:
- _is_price_condition_met now implements the robust "level crossing" algorithm,
  correctly handling price gaps and high volatility for all trade and order types.
- This is the definitive fix for the false positive triggers and potential missed targets.
- All previous hardening features (DB retry loop, debouncing) are retained.
"""

import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional
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
                log.critical(
                    "CRITICAL: Failed to read triggers from repository. Retrying in %ds...",
                    retry_delay,
                    exc_info=True
                )
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
            for sym, triggers in new_triggers.items():
                seen = set()
                unique = []
                for t in triggers:
                    key = (t.get("rec_id"), t.get("type"), float(t.get("price") or 0.0))
                    if key in seen:
                        continue
                    seen.add(key)
                    unique.append(t)
                new_triggers[sym] = unique
            self.active_triggers = new_triggers

        total_recs = len(trigger_data) if trigger_data is not None else 0
        log.info("✅ Trigger index built successfully: %d recommendations across %d symbols.", total_recs, len(new_triggers))

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

        try:
            status_norm = status.name if hasattr(status, "name") else str(status).upper()
        except Exception:
            status_norm = str(status).upper()

        if status_norm in ("0", "PENDING"):
            try:
                price = float(item.get("entry") or 0.0)
            except Exception:
                price = 0.0
            trigger_dict[asset].append({
                "rec_id": rec_id, "user_id": user_id, "side": side,
                "type": "ENTRY", "price": price, "order_type": item.get("order_type")
            })
            return

        if status_norm in ("1", "ACTIVE"):
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
        try:
            loop = asyncio.get_running_loop()
            if self._processing_task is None or self._processing_task.done():
                self._processing_task = loop.create_task(self._process_queue())
            if self._index_sync_task is None or self._index_sync_task.done():
                self._index_sync_task = loop.create_task(self._run_index_sync())
            try:
                if hasattr(self.streamer, "start"):
                    self.streamer.start()
            except Exception:
                log.exception("Streamer.start() failed in event loop context.")
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
        try:
            if hasattr(self.streamer, "stop"):
                self.streamer.stop()
        except Exception:
            log.exception("Error stopping streamer.")

        try:
            if self._processing_task and not self._processing_task.done():
                self._processing_task.cancel()
            if self._index_sync_task and not self._index_sync_task.done():
                self._index_sync_task.cancel()
        except Exception:
            log.exception("Error cancelling tasks in main loop.")

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
        """
        ✅ FINAL & ROBUST LOGIC: This function now correctly handles price gaps and slippage
        by checking if the price has crossed the target level based on the trade's direction
        and the condition type.
        """
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()

        # --- Logic for LONG trades ---
        if side_upper == "LONG":
            # For TPs, the price needs to go UP to or past the target.
            # We check if the candle's high has reached or exceeded the target.
            if cond.startswith("TP"):
                return high_price >= target_price

            # For SL or Profit Stop, the price needs to go DOWN to or past the target.
            # We check if the candle's low has reached or fallen below the target.
            if cond in ("SL", "PROFIT_STOP"):
                return low_price <= target_price

            # Entry logic depends on the order type.
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                # A LIMIT buy triggers when the price drops to the entry level.
                if ot.endswith("LIMIT"):
                    return low_price <= target_price
                # A STOP_MARKET buy triggers when the price breaks above the entry level.
                if ot.endswith("STOP_MARKET"):
                    return high_price >= target_price
                # Fallback for generic or unknown entry types (should not happen).
                return low_price <= target_price or high_price >= target_price

        # --- Logic for SHORT trades ---
        if side_upper == "SHORT":
            # For TPs, the price needs to go DOWN to or past the target.
            # We check if the candle's low has reached or fallen below the target.
            if cond.startswith("TP"):
                return low_price <= target_price

            # For SL or Profit Stop, the price needs to go UP to or past the target.
            # We check if the candle's high has reached or exceeded the target.
            if cond in ("SL", "PROFIT_STOP"):
                return high_price >= target_price

            # Entry logic depends on the order type.
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                # A LIMIT sell triggers when the price rises to the entry level.
                if ot.endswith("LIMIT"):
                    return high_price >= target_price
                # A STOP_MARKET sell triggers when the price breaks below the entry level.
                if ot.endswith("STOP_MARKET"):
                    return low_price <= target_price
                # Fallback for generic or unknown entry types.
                return low_price <= target_price or high_price >= target_price

        # If side is unknown or condition is unhandled, return False.
        return False

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        async with self._triggers_lock:
            triggers_for_symbol = list(self.active_triggers.get((symbol or "").upper(), []))

        if not triggers_for_symbol:
            return

        triggered_ids = set()
        now_ts = time.time()
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

            rec_id = int(trigger.get("rec_id") or 0)
            ttype_raw = (trigger.get("type") or "").upper()
            
            last_map = self._last_processed.setdefault(rec_id, {})
            last_ts = last_map.get(ttype_raw)
            if last_ts and (now_ts - last_ts) < self._debounce_seconds:
                log.debug("Debounced duplicate event for rec %s type %s (%.3fs since last).", rec_id, ttype_raw, now_ts - last_ts)
                continue
            
            last_map[ttype_raw] = now_ts

            if rec_id in triggered_ids:
                if ttype_raw in triggered_ids:
                    continue
            triggered_ids.add(rec_id)

            log.info("Trigger HIT for Rec #%s: Type=%s, Symbol=%s, Range=[%s,%s], Target=%s", rec_id, ttype_raw, symbol, low_price, high_price, execution_price)
            try:
                if ttype_raw == "ENTRY":
                    await self.trade_service.process_activation_event(rec_id)
                elif self._tp_re.match(ttype_raw):
                    m = self._tp_re.match(ttype_raw)
                    try:
                        idx = int(m.group(1))
                    except Exception:
                        idx = 1
                    await self.trade_service.process_tp_hit_event(rec_id, trigger.get("user_id"), idx, execution_price)
                elif ttype_raw == "SL":
                    await self.trade_service.process_sl_hit_event(rec_id, trigger.get("user_id"), execution_price)
                elif ttype_raw == "PROFIT_STOP":
                    await self.trade_service.process_profit_stop_hit_event(rec_id, trigger.get("user_id"), execution_price)
                else:
                    log.debug("Unhandled trigger type: %s", ttype_raw)
            except Exception:
                log.exception("Failed processing hit event for rec %s", rec_id)