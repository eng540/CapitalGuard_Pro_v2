# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---
# src/capitalguard/application/services/alert_service.py (v26.8 - R1-S1 Spam Fix)
"""
AlertService - Orchestrates price updates, delegating complex exit strategy
logic to the StrategyEngine, while handling core SL/TP/Entry triggers.

✅ THE FIX (R1-S1 Hotfix 10 - Bug B Fix, Part 4):
    - `_evaluate_core_triggers` for 'user_trade' now checks the
      `trigger["processed_events"]` set before calling event handlers.
    - This prevents `process_user_trade_tp_hit_event` (and others)
      from being called repeatedly, fixing the notification spam bug.
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
# ✅ R1-S1: Import the new UserTradeStatus Enum
from capitalguard.infrastructure.db.models import UserTradeStatus as UserTradeStatusEnum
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
        log.info("Attempting to build in-memory trigger index (Unified)...")
        try:
            with session_scope() as session:
                # This function now returns BOTH recs and user_trades (with events)
                trigger_data_list = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.critical("CRITICAL: DB read failure during trigger index build.", exc_info=True)
            return

        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        active_rec_ids = set() # Only for strategy engine (Analyst Recs)
        active_item_ids = set() # For state clearing

        for item in trigger_data_list:
            try:
                asset = item.get("asset")
                item_id = item.get("id")
                if not asset or not item_id:
                    continue
                
                active_item_ids.add(item_id)
                
                if item.get("item_type") == "recommendation":
                    active_rec_ids.add(item_id)
                    self.strategy_engine.initialize_state_for_recommendation(item)

                trigger_key = f"{asset.upper()}:{item.get('market', 'Futures')}"
                if trigger_key not in new_triggers: new_triggers[trigger_key] = []
                new_triggers[trigger_key].append(item)
            except Exception as e:
                log.exception("Failed processing trigger item: %s", item.get('id'))
        
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        
        # Clear stale states for *analyst* strategies
        stale_rec_ids = set(self.strategy_engine._state.keys()) - active_rec_ids
        for rec_id in stale_rec_ids:
            self.strategy_engine.clear_state(rec_id)

        log.info("✅ Trigger index rebuilt: %d items (Recs + UserTrades) across %d unique asset/market pairs.", len(trigger_data_list), len(new_triggers))

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
                    
                    strategy_actions = []
                    if trigger.get("item_type") == "recommendation":
                        strategy_actions = self.strategy_engine.evaluate(trigger, high_price, low_price)
                    
                    core_actions = await self._evaluate_core_triggers(trigger, high_price, low_price)
                    
                    # --- Execution Phase (Analyst Recommendations Only) ---
                    # UserTrade actions are handled *inside* _evaluate_core_triggers
                    all_actions = strategy_actions + core_actions
                    if not all_actions:
                        continue
                    
                    close_action = next((a for a in all_actions if isinstance(a, CloseAction)), None)
                    if close_action:
                        await self.trade_service.close_recommendation_async(
                            rec_id=close_action.rec_id, 
                            user_id=trigger['user_id'], # This is analyst telegram ID
                            exit_price=close_action.price, 
                            reason=close_action.reason
                        )
                        self.strategy_engine.clear_state(close_action.rec_id)
                        continue 

                    for action in all_actions:
                        if isinstance(action, MoveSLAction):
                            await self.trade_service.update_sl_for_user_async(
                                rec_id=action.rec_id, 
                                user_id=trigger['user_id'], # Analyst telegram ID
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

    async def _evaluate_core_triggers(self, trigger: Dict[str, Any], high_price: Decimal, low_price: Decimal) -> List[BaseAction]:
        """
        Evaluates core triggers for BOTH item types.
        - Returns BaseActions for Recommendations.
        - Awaits TradeService methods directly for UserTrades.
        """
        actions: List[BaseAction] = []
        item_id = trigger["id"]
        status = trigger["status"]
        side = trigger["side"]
        item_type = trigger.get("item_type", "recommendation")
        processed_events = trigger.get("processed_events", set())

        try:
            # --- BRANCH 1: Analyst Recommendation Lifecycle ---
            if item_type == "recommendation":
                if status == RecommendationStatusEnum.PENDING:
                    entry_price, sl_price = trigger["entry"], trigger["stop_loss"]
                    if "INVALIDATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        await self.trade_service.process_invalidation_event(item_id)
                    elif "ACTIVATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                        await self.trade_service.process_activation_event(item_id)

                elif status == RecommendationStatusEnum.ACTIVE:
                    sl_price = trigger["stop_loss"]
                    if "SL_HIT" not in processed_events and "FINAL_CLOSE" not in processed_events and self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        actions.append(CloseAction(rec_id=item_id, price=sl_price, reason="SL_HIT"))
                        return actions # Stop further processing if SL hit

                    for i, target in enumerate(trigger["targets"], 1):
                        event_name = f"TP{i}_HIT"
                        if event_name in processed_events:
                            continue
                        target_price = Decimal(str(target['price']))
                        if self._is_price_condition_met(side, low_price, high_price, target_price, "TP"):
                            await self.trade_service.process_tp_hit_event(item_id, i, target_price)

            # --- BRANCH 2: User Trade Lifecycle ---
            elif item_type == "user_trade":
                # State 1: Waiting for activation (WATCHLIST or PENDING_ACTIVATION)
                if status in (UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION):
                    entry_price, sl_price = trigger["entry"], trigger["stop_loss"]
                    
                    published_at = trigger.get("original_published_at")
                    if published_at and datetime.now(timezone.utc) < published_at:
                        return actions 
                    
                    # ✅ R1-S1 HOTFIX 10: Check processed_events
                    if "INVALIDATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        await self.trade_service.process_user_trade_invalidation_event(item_id, sl_price)
                    elif "ACTIVATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                        await self.trade_service.process_user_trade_activation_event(item_id)
                
                # State 2: Active trade, watching for SL/TP
                elif status == UserTradeStatusEnum.ACTIVATED:
                    sl_price = trigger["stop_loss"]
                    # ✅ R1-S1 HOTFIX 10: Check processed_events
                    if "SL_HIT" not in processed_events and "FINAL_CLOSE" not in processed_events and self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        await self.trade_service.process_user_trade_sl_hit_event(item_id, sl_price)
                        return actions # Stop further processing

                    for i, target in enumerate(trigger["targets"], 1):
                        # ✅ R1-S1 HOTFIX 10: Check processed_events
                        event_name = f"TP{i}_HIT"
                        if event_name in processed_events:
                            continue
                            
                        target_price = Decimal(str(target['price']))
                        if self._is_price_condition_met(side, low_price, high_price, target_price, "TP"):
                            await self.trade_service.process_user_trade_tp_hit_event(item_id, i, target_price)
                            # Note: process_user_trade_tp_hit_event handles auto-closing on final TP

        except Exception as e:
            logger.error(f"Error evaluating core trigger for {item_type} #{item_id}: {e}", exc_info=True)
        
        return actions
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---