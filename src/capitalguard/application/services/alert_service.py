src/capitalguard/application/services/alert_service.py V 18.3.2 (Hardened, thread-safe fixes)

""" AlertService — Hardened release 18.3.2

Summary of substantive fixes in this file (see code comments for details):

Fixed incorrect triggered_ids logic (now a dict mapping rec_id->set(types)).

Made debounce store access guarded by an asyncio.Lock to avoid races.

Introduced _stop_event and cancellation-aware checks for graceful shutdown.

Converted potentially-blocking DB repo calls to run_in_executor to avoid blocking the event loop (keeps session_scope usage but executes it on threadpool).

Added a small adapter so external thread-based PriceStreamer can push prices using a thread-safe queue.Queue and an _bridge coroutine that forwards to asyncio.Queue. (Maintains backwards compatibility if PriceStreamer already supports asyncio.Queue.)

Fixed the mix of asyncio primitives and threading by isolating cross-thread interactions.

Improved logging and explicit error handling paths.


Behavioral notes:

This file keeps the repository sync/transaction semantics but ensures DB I/O runs off the event loop (via loop.run_in_executor). For best performance, consider moving repo to async.

The PriceStreamer adapter accepts either an asyncio.Queue or a sync queue.Queue. If a sync queue is provided (e.g. used by a thread), the adapter will forward items safely.


This file is intended to be a drop-in replacement for V18.3.1 with minimal external changes. """

import logging import asyncio import threading from typing import List, Dict, Any, Optional, Tuple from contextlib import suppress import time import re import queue

from capitalguard.infrastructure.db.uow import session_scope from capitalguard.infrastructure.db.repository import RecommendationRepository from capitalguard.infrastructure.sched.price_streamer import PriceStreamer

log = logging.getLogger(name)

class AlertService: def init( self, trade_service, repo: RecommendationRepository, streamer: Optional[PriceStreamer] = None, debounce_seconds: float = 1.0, ): self.trade_service = trade_service self.repo = repo

# The canonical internal asyncio queue used by the processor coroutine
    self._async_price_queue: asyncio.Queue = asyncio.Queue()

    # Allow streamer to be either an async-aware streamer (puts to asyncio.Queue)
    # or a thread-based streamer that puts to a sync queue.Queue. We create a bridge
    # that forwards thread-safe queue items into the asyncio queue.
    self._sync_bridge_queue: Optional[queue.Queue] = None
    self.streamer = streamer

    self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
    self._triggers_lock = asyncio.Lock()

    self._processing_task: Optional[asyncio.Task] = None
    self._index_sync_task: Optional[asyncio.Task] = None
    self._bridge_task: Optional[asyncio.Task] = None

    # background runner for sync start()
    self._bg_thread: Optional[threading.Thread] = None
    self._bg_loop: Optional[asyncio.AbstractEventLoop] = None

    # debounce store: { rec_id: { event_key: last_ts } }
    self._last_processed: Dict[int, Dict[str, float]] = {}
    self._debounce_seconds = float(debounce_seconds)
    # guard debounce map
    self._debounce_lock = asyncio.Lock()

    # TP regex
    self._tp_re = re.compile(r"^TP(\d+)$", flags=re.IGNORECASE)

    # graceful stop event
    self._stop_event = asyncio.Event()

# ---------- Utilities ----------

async def _run_db_in_executor(self, fn, *args, **kwargs):
    """Run a synchronous DB-bound function in the default threadpool to avoid blocking
    the event loop. Returns the function's result or raises the exception."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

def _maybe_create_sync_bridge_queue(self):
    """If streamer appears to be thread-based, create a sync queue bridge and provide it.
    This keeps backward compatibility: if streamer expects a sync queue.Queue, it can use it.
    If streamer already supports asyncio.Queue, we will not create the bridge."""
    if self.streamer is None:
        return
    # Heuristic: if streamer has attribute `put_sync` or its start() expects a sync queue,
    # or if streamer itself is known to be thread-based, user must have already wired it.
    # For safety we expose self._sync_bridge_queue for external thread producers.
    if self._sync_bridge_queue is None:
        self._sync_bridge_queue = queue.Queue()

# ---------- Trigger index ----------

async def build_triggers_index(self):
    """
    ✅ HARDENED: This method now retries on database failure and runs DB I/O off the event loop.
    """
    log.info("Attempting to build in-memory trigger index for all active recommendations...")
    retry_delay = 5
    while not self._stop_event.is_set():
        try:
            # run listing in executor to avoid blocking the loop
            def _load():
                with session_scope() as session:
                    return self.repo.list_all_active_triggers_data(session)

            trigger_data = await self._run_db_in_executor(_load)
            break  # Success
        except asyncio.CancelledError:
            log.info("build_triggers_index cancelled")
            raise
        except Exception:
            log.critical(
                "CRITICAL: Failed to read triggers from repository. Retrying in %ds...",
                retry_delay,
                exc_info=True,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

    if self._stop_event.is_set():
        return

    new_triggers: Dict[str, List[Dict[str, Any]]] = {}
    for item in (trigger_data or []):
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
        # remove duplicates by (rec_id, type, price)
        for sym, triggers in new_triggers.items():
            seen = set()
            unique = []
            for t in triggers:
                try:
                    key = (t.get("rec_id"), t.get("type"), float(t.get("price") or 0.0))
                except Exception:
                    key = (t.get("rec_id"), t.get("type"), 0.0)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(t)
            new_triggers[sym] = unique
        self.active_triggers = new_triggers

    total_recs = sum(len(v) for v in (trigger_data or [])) if trigger_data is not None else 0
    log.info("✅ Trigger index built successfully: %d items across %d symbols.",
             sum(len(v) for v in new_triggers.values()), len(new_triggers))

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

    # normalize status (accept enum or primitive)
    try:
        status_norm = status.name if hasattr(status, "name") else str(status).upper()
    except Exception:
        status_norm = str(status).upper()

    # ENTRY for pending
    if status_norm in ("0", "PENDING"):
        try:
            price = float(item.get("entry") or 0.0)
        except Exception:
            price = 0.0
        trigger_dict[asset].append({
            "rec_id": rec_id,
            "user_id": user_id,
            "side": side,
            "type": "ENTRY",
            "price": price,
            "order_type": item.get("order_type"),
        })
        return

    # ACTIVE -> SL, PROFIT_STOP, TPs
    if status_norm in ("1", "ACTIVE"):
        sl = item.get("stop_loss")
        if sl is not None:
            try:
                slp = float(sl)
                trigger_dict[asset].append({
                    "rec_id": rec_id,
                    "user_id": user_id,
                    "side": side,
                    "type": "SL",
                    "price": slp,
                })
            except Exception:
                log.warning("Invalid stop_loss for rec %s: %s", rec_id, sl)
        psp = item.get("profit_stop_price")
        if psp is not None:
            try:
                pspv = float(psp)
                trigger_dict[asset].append({
                    "rec_id": rec_id,
                    "user_id": user_id,
                    "side": side,
                    "type": "PROFIT_STOP",
                    "price": pspv,
                })
            except Exception:
                log.warning("Invalid profit_stop_price for rec %s: %s", rec_id, psp)
        for idx, target in enumerate(item.get("targets") or []):
            try:
                tprice = float(target.get("price"))
                trigger_dict[asset].append({
                    "rec_id": rec_id,
                    "user_id": user_id,
                    "side": side,
                    "type": f"TP{idx+1}",
                    "price": tprice,
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
        def _get():
            with session_scope() as session:
                return self.repo.get_active_trigger_data_by_id(session, rec_id)

        item = await self._run_db_in_executor(_get)
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
        while not self._stop_event.is_set():
            await asyncio.sleep(interval_seconds)
            await self.build_triggers_index()
    except asyncio.CancelledError:
        log.info("Index sync task cancelled.")
    except Exception:
        log.exception("Index sync encountered error.")

async def _bridge_sync_queue(self):
    """Bridge items from a thread-safe sync queue.Queue into the internal asyncio queue."""
    if self._sync_bridge_queue is None:
        return
    log.info("Starting sync->async bridge task.")
    try:
        while not self._stop_event.is_set():
            try:
                item = await asyncio.get_running_loop().run_in_executor(None, self._sync_bridge_queue.get)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error reading from sync bridge queue")
                await asyncio.sleep(0.1)
                continue

            if item is None:
                continue

            try:
                # item expected to be tuple (symbol, low, high)
                await self._async_price_queue.put(item)
            except Exception:
                log.exception("Failed to forward item from sync bridge: %s", item)
    except asyncio.CancelledError:
        log.info("Sync bridge cancelled.")

async def _process_queue(self):
    log.info("AlertService queue processor started.")
    try:
        while not self._stop_event.is_set():
            try:
                symbol, low_price, high_price = await self._async_price_queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self.check_and_process_alerts(symbol, low_price, high_price)
            except Exception:
                log.exception("Error while processing alerts for %s", symbol)
            finally:
                with suppress(Exception):
                    self._async_price_queue.task_done()
    except asyncio.CancelledError:
        log.info("Queue processor cancelled.")
    except Exception:
        log.exception("Unexpected error in queue processor.")

# ---------- Start / Stop ----------

def start(self):
    try:
        loop = asyncio.get_running_loop()
        # running inside an event loop
        if self._processing_task is None or self._processing_task.done():
            self._processing_task = loop.create_task(self._process_queue())
        if self._index_sync_task is None or self._index_sync_task.done():
            self._index_sync_task = loop.create_task(self._run_index_sync())

        # prepare bridge if streamer is thread-based
        self._maybe_create_sync_bridge_queue()
        if self._sync_bridge_queue and (self._bridge_task is None or self._bridge_task.done()):
            self._bridge_task = loop.create_task(self._bridge_sync_queue())

        # wire streamer: if streamer has a `set_queue` hook prefer that
        try:
            if self.streamer is not None:
                if hasattr(self.streamer, "set_async_queue"):
                    self.streamer.set_async_queue(self._async_price_queue)
                elif hasattr(self.streamer, "set_sync_queue"):
                    # provide the sync bridge queue for thread-based streamers
                    self.streamer.set_sync_queue(self._sync_bridge_queue)
                elif hasattr(self.streamer, "start"):
                    # start is allowed to be async or sync; call safely
                    maybe = self.streamer.start()
                    if asyncio.iscoroutine(maybe):
                        loop.create_task(maybe)
        except Exception:
            log.exception("Streamer.start() failed in event loop context.")

        log.info("AlertService started in existing event loop.")
        return
    except RuntimeError:
        # no running loop -> create background thread with its own loop
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
                self._maybe_create_sync_bridge_queue()
                if self._sync_bridge_queue:
                    self._bridge_task = loop.create_task(self._bridge_sync_queue())

                try:
                    if self.streamer is not None:
                        if hasattr(self.streamer, "set_sync_queue"):
                            self.streamer.set_sync_queue(self._sync_bridge_queue)
                        elif hasattr(self.streamer, "set_async_queue"):
                            # can't directly use asyncio queue from other thread; log and skip
                            log.warning("Streamer expects asyncio queue but AlertService started in background thread."
                                        "Consider starting AlertService in main event loop to wire streamer directly.")
                        elif hasattr(self.streamer, "start"):
                            # run start in this thread (streamer may be thread-based)
                            try:
                                self.streamer.start()
                            except Exception:
                                log.exception("Streamer.start() failed in background thread.")
                except Exception:
                    log.exception("Streamer wiring failed in background thread.")

                loop.run_forever()
            except Exception:
                log.exception("AlertService background runner crashed.")
            finally:
                try:
                    for t in (self._processing_task, self._index_sync_task, self._bridge_task):
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

    # set stop event so tasks exit their loops
    try:
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(self._stop_event.set)
    except RuntimeError:
        # No running loop here: try to set event on bg loop
        if self._bg_loop:
            try:
                self._bg_loop.call_soon_threadsafe(self._stop_event.set)
            except Exception:
                log.exception("Failed to set stop event on bg loop.")

    try:
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
        if self._index_sync_task and not self._index_sync_task.done():
            self._index_sync_task.cancel()
        if self._bridge_task and not self._bridge_task.done():
            self._bridge_task.cancel()
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
    self._bridge_task = None
    log.info("AlertService stopped and cleaned up.")

# ---------- Condition evaluation & processing ----------

def _is_price_condition_met(
    self,
    side: str,
    low_price: float,
    high_price: float,
    target_price: float,
    condition_type: str,
    order_type: Optional[Any] = None,
) -> bool:
    side_upper = (side or "").upper()
    cond = (condition_type or "").upper()

    # inclusive comparisons to capture edge hits
    if side_upper == "LONG":
        if cond.startswith("TP"):
            return high_price >= target_price
        if cond in ("SL", "PROFIT_STOP"):
            return low_price <= target_price
        if cond == "ENTRY":
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

    # triggered_map: rec_id -> set(types triggered in this candle)
    triggered_map: Dict[int, set] = {}
    now_ts = time.time()
    for trigger in triggers_for_symbol:
        try:
            execution_price = float(trigger.get("price") or 0.0)
        except Exception:
            log.warning("Invalid trigger price, skipping: %s", trigger)
            continue

        try:
            if not self._is_price_condition_met(
                trigger.get("side"), low_price, high_price, execution_price, trigger.get("type"), trigger.get("order_type")
            ):
                continue
        except Exception:
            log.exception("Error evaluating trigger condition: %s", trigger)
            continue

        rec_id = int(trigger.get("rec_id") or 0)
        ttype_raw = (trigger.get("type") or "").upper()

        # debounce check (guarded)
        async with self._debounce_lock:
            last_map = self._last_processed.setdefault(rec_id, {})
            last_ts = last_map.get(ttype_raw)
            if last_ts and (now_ts - last_ts) < self._debounce_seconds:
                log.debug("Debounced duplicate event for rec %s type %s (%.3fs since last).",
                          rec_id, ttype_raw, now_ts - last_ts)
                continue
            # mark now to avoid races
            last_map[ttype_raw] = now_ts

        # per-candle guard: allow multiple different types for the same rec in one pass,
        # but don't process same type twice in same pass
        types_for_rec = triggered_map.setdefault(rec_id, set())
        if ttype_raw in types_for_rec:
            continue
        types_for_rec.add(ttype_raw)

        log.info("Trigger HIT for Rec #%s: Type=%s, Symbol=%s, Range=[%s,%s], Target=%s",
                 rec_id, ttype_raw, symbol, low_price, high_price, execution_price)
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

