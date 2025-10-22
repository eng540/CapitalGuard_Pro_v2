# src/capitalguard/application/services/alert_service.py (v26.2 - DetachedInstanceError Hotfix)
"""
AlertService - Orchestrates price updates, delegating complex exit strategy
logic to the StrategyEngine, while handling core SL/TP/Entry triggers.
✅ HOTFIX: Now works with dictionaries from the repository to prevent DetachedInstanceError.
"""

import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional
from decimal import Decimal
import time

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer
from capitalguard.infrastructure.db.models import RecommendationStatusEnum
from capitalguard.application.strategy.engine import StrategyEngine

if False:
    from .trade_service import TradeService
    from .price_service import PriceService

log = logging.getLogger(__name__)

class AlertService:
    def __init__(
        self, 
        trade_service: "TradeService", 
        price_service: "PriceService", 
        repo: RecommendationRepository,
        strategy_engine: StrategyEngine,
        streamer: Optional[PriceStreamer] = None
    ):
        self.trade_service = trade_service
        self.price_service = price_service
        self.repo = repo
        self.strategy_engine = strategy_engine
        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)
        
        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()
        
        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None

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
                if hasattr(self.streamer, "start"): self.streamer.start(loop=loop)
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
                trigger_data_list = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.critical("CRITICAL: DB read failure during trigger index build.", exc_info=True)
            return

        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        active_rec_ids = set()

        for item in trigger_data_list:
            try:
                asset = item.get("asset")
                if not asset: continue
                
                active_rec_ids.add(item['id'])
                self.strategy_engine.initialize_state_for_recommendation(item)

                trigger_key = f"{asset.upper()}:{item.get('market', 'Futures')}"
                if trigger_key not in new_triggers: new_triggers[trigger_key] = []
                new_triggers[trigger_key].append(item)
            except Exception:
                log.exception("Failed processing trigger item: %s", item)
        
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        
        stale_ids = set(self.strategy_engine._state.keys()) - active_rec_ids
        for rec_id in stale_ids:
            self.strategy_engine._state.pop(rec_id, None)

        log.info("✅ Trigger index rebuilt: %d items across %d unique asset/market pairs.", len(trigger_data_list), len(new_triggers))

    async def _run_index_sync(self, interval_seconds: int = 60):
        log.info("Index sync task started (interval=%ss).", interval_seconds)
        while True:
            await asyncio.sleep(interval_seconds)
            await self.build_triggers_index()

    async def _process_queue(self):
        log.info("AlertService queue processor started.")
        while True:
            try:
                symbol, market, low_price, high_price = await self.price_queue.get()
                trigger_key = f"{symbol.upper()}:{market}"
                
                async with self._triggers_lock:
                    triggers_for_key = self.active_triggers.get(trigger_key, [])

                if not triggers_for_key:
                    self.price_queue.task_done()
                    continue

                # Evaluate all triggers for this asset
                for trigger in triggers_for_key:
                    # Delegate advanced strategy evaluation
                    await self.strategy_engine.evaluate_recommendation(trigger, Decimal(str(high_price)), Decimal(str(low_price)))
                    
                    # Handle core triggers
                    await self.check_and_process_core_alerts(trigger, Decimal(str(low_price)), Decimal(str(high_price)))
                
                self.price_queue.task_done()
            except asyncio.CancelledError:
                log.info("Queue processor cancelled.")
                break
            except Exception:
                log.exception("Unexpected error in queue processor.")

    def stop(self):
        if hasattr(self.streamer, "stop"): self.streamer.stop()
        if self._bg_loop:
            tasks = [self._processing_task, self._index_sync_task]
            for t in tasks:
                if t and not t.done(): self._bg_loop.call_soon_threadsafe(t.cancel)
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        if self._bg_thread: self._bg_thread.join(timeout=5.0)
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

    async def check_and_process_core_alerts(self, trigger: Dict[str, Any], low_price: Decimal, high_price: Decimal):
        item_id = trigger["id"]
        status = trigger["status"]
        side = trigger["side"]
        
        try:
            if status == RecommendationStatusEnum.PENDING:
                entry_price, sl_price = trigger["entry"], trigger["stop_loss"]
                if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                    await self.trade_service.process_invalidation_event(item_id)
                elif self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                    await self.trade_service.process_activation_event(item_id)

            elif status == RecommendationStatusEnum.ACTIVE:
                sl_price = trigger["stop_loss"]
                if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                    await self.trade_service.process_sl_hit_event(item_id, sl_price)
                    return # Stop further checks if SL is hit

                for i, target in enumerate(trigger["targets"], 1):
                    if f"TP{i}_HIT" in trigger["processed_events"]: continue
                    target_price = Decimal(str(target['price']))
                    if self._is_price_condition_met(side, low_price, high_price, target_price, "TP"):
                        await self.trade_service.process_tp_hit_event(item_id, i, target_price)
        
        except Exception as e:
            log.error("Error processing core trigger for item #%s: %s", item_id, e, exc_info=True)