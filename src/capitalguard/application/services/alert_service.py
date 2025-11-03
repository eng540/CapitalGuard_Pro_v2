# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---
# src/capitalguard/application/services/alert_service.py (v26.6 - Notification Reliability Hotfix)
"""
AlertService - Orchestrates price updates, delegating complex exit strategy
logic to the StrategyEngine, while handling core SL/TP/Entry triggers.
✅ HOTFIX (v26.6): Refactored `_evaluate_core_triggers` to be `async def`.
✅ HOTFIX (v26.6): Replaced all unreliable `asyncio.create_task` calls with direct `await`
       calls to `trade_service` event processors. This fixes the critical bug
       where notifications (TP hit, SL hit, Activation) were lost.
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
from capitalguard.application.strategy.engine import StrategyEngine, BaseAction, CloseAction, MoveSLAction

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
        """Builds the in-memory index of all active triggers from the database."""
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
                
                rec_id = item['id']
                active_rec_ids.add(rec_id)
                self.strategy_engine.initialize_state_for_recommendation(item)

                trigger_key = f"{asset.upper()}:{item.get('market', 'Futures')}"
                if trigger_key not in new_triggers: new_triggers[trigger_key] = []
                new_triggers[trigger_key].append(item)
            except Exception as e:
                log.exception("Failed processing trigger item: %s", item.get('id'))
        
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        
        stale_ids = set(self.strategy_engine._state.keys()) - active_rec_ids
        for rec_id in stale_ids:
            self.strategy_engine.clear_state(rec_id)

        log.info("✅ Trigger index rebuilt: %d items across %d unique asset/market pairs.", len(trigger_data_list), len(new_triggers))

    async def _run_index_sync(self, interval_seconds: int = 60):
        """Periodically rebuilds the trigger index to catch new or closed trades."""
        log.info("Index sync task started (interval=%ss).", interval_seconds)
        while True:
            await asyncio.sleep(interval_seconds)
            await self.build_triggers_index()

    async def _process_queue(self):
        """Main processing loop that consumes price updates from the queue."""
        log.info("AlertService queue processor started.")
        while True:
            try:
                symbol, market, low_price_str, high_price_str = await self.price_queue.get()
                trigger_key = f"{symbol.upper()}:{market}"
                low_price, high_price = Decimal(str(low_price_str)), Decimal(str(high_price_str))
                
                async with self._triggers_lock:
                    triggers_for_key = self.active_triggers.get(trigger_key, [])

                if not triggers_for_key:
                    self.price_queue.task_done()
                    continue

                for trigger in triggers_for_key:
                    # --- Evaluation Phase ---
                    strategy_actions = self.strategy_engine.evaluate(trigger, high_price, low_price)
                    
                    # ✅ HOTFIX: Call the new async version and await it
                    core_actions = await self._evaluate_core_triggers(trigger, high_price, low_price)
                    
                    # --- Execution Phase ---
                    all_actions = strategy_actions + core_actions
                    if not all_actions: continue

                    close_action = next((a for a in all_actions if isinstance(a, CloseAction)), None)
                    if close_action:
                        # This is a high-priority action that must complete
                        await self.trade_service.close_recommendation_async(
                            rec_id=close_action.rec_id, 
                            user_id=trigger['user_id'], 
                            exit_price=close_action.price, 
                            reason=close_action.reason
                        )
                        self.strategy_engine.clear_state(close_action.rec_id)
                        continue # Move to next trigger, this one is closed

                    for action in all_actions:
                        if isinstance(action, MoveSLAction):
                            # This is also high-priority
                            await self.trade_service.update_sl_for_user_async(
                                rec_id=action.rec_id, 
                                user_id=trigger['user_id'], 
                                new_sl=action.new_sl
                            )
                
                self.price_queue.task_done()
            except asyncio.CancelledError:
                log.info("Queue processor cancelled.")
                break
            except Exception:
                log.exception("Unexpected error in queue processor.")

    def stop(self):
        """Stops the AlertService and its background tasks."""
        if hasattr(self.streamer, "stop"): self.streamer.stop()
        if self._bg_loop:
            tasks = [self._processing_task, self._index_sync_task]
            for t in tasks:
                if t and not t.done(): self._bg_loop.call_soon_threadsafe(t.cancel)
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        if self._bg_thread: self._bg_thread.join(timeout=5.0)
        log.info("AlertService stopped and cleaned up.")

    def _is_price_condition_met(self, side: str, low_price: Decimal, high_price: Decimal, target_price: Decimal, condition_type: str) -> bool:
        """Checks if a price condition (SL, TP, Entry) has been met."""
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

    # ✅ HOTFIX: Changed to `async def`
    async def _evaluate_core_triggers(self, trigger: Dict[str, Any], high_price: Decimal, low_price: Decimal) -> List[BaseAction]:
        """
        Evaluates core triggers and returns a list of actions.
        This function now reliably awaits event processing.
        """
        actions: List[BaseAction] = []
        item_id = trigger["id"]
        status = trigger["status"]
        side = trigger["side"]
        
        try:
            if status == RecommendationStatusEnum.PENDING:
                entry_price, sl_price = trigger["entry"], trigger["stop_loss"]
                if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                    # ✅ HOTFIX: Removed asyncio.create_task, use direct await
                    await self.trade_service.process_invalidation_event(item_id)
                elif self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                    # ✅ HOTFIX: Removed asyncio.create_task, use direct await
                    await self.trade_service.process_activation_event(item_id)

            elif status == RecommendationStatusEnum.ACTIVE:
                sl_price = trigger["stop_loss"]
                if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                    # Return a CloseAction for the main loop to process
                    actions.append(CloseAction(rec_id=item_id, price=sl_price, reason="SL_HIT"))
                    return actions # Stop further processing if SL hit

                for i, target in enumerate(trigger["targets"], 1):
                    if f"TP{i}_HIT" in trigger["processed_events"]: continue
                    target_price = Decimal(str(target['price']))
                    if self._is_price_condition_met(side, low_price, high_price, target_price, "TP"):
                        # ✅ HOTFIX: Removed asyncio.create_task, use direct await
                        await self.trade_service.process_tp_hit_event(item_id, i, target_price)
        
        except Exception as e:
            log.error("Error evaluating core trigger for item #%s: %s", item_id, e, exc_info=True)
        
        return actions
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---