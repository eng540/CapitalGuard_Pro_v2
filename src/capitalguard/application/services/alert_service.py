# src/capitalguard/application/services/alert_service.py (v26.1 - StrategyEngine Integration)
"""
AlertService - Orchestrates price updates, delegating complex exit strategy
logic to the StrategyEngine, while handling core SL/TP/Entry triggers.
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
from capitalguard.infrastructure.db.models import Recommendation, RecommendationStatusEnum
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
                if hasattr(self.streamer, "start"):
                    self.streamer.start(loop=loop)
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
                # Fetch all active recommendations (PENDING or ACTIVE)
                active_recs = self.repo.get_all_active_recs(session)
        except Exception:
            log.critical("CRITICAL: DB read failure during trigger index build.", exc_info=True)
            return

        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        active_rec_ids = set()

        for rec in active_recs:
            try:
                asset = rec.asset
                if not asset: continue
                
                active_rec_ids.add(rec.id)
                # Initialize state in StrategyEngine for this active recommendation
                self.strategy_engine.initialize_state_for_recommendation(rec)

                trigger_key = f"{asset.upper()}:{rec.market or 'Futures'}"
                if trigger_key not in new_triggers:
                    new_triggers[trigger_key] = []
                
                # Prepare a dictionary with all necessary data for evaluation
                trigger_data = {
                    "id": rec.id,
                    "user_id": str(rec.analyst.telegram_user_id) if rec.analyst else None,
                    "asset": rec.asset,
                    "side": rec.side,
                    "entry": Decimal(str(rec.entry)),
                    "stop_loss": Decimal(str(rec.stop_loss)),
                    "targets": rec.targets,
                    "status": rec.status,
                    "processed_events": {e.event_type for e in rec.events},
                }
                new_triggers[trigger_key].append(trigger_data)
            except Exception:
                log.exception("Failed processing trigger for recommendation: %s", rec.id)
        
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        
        # Clean up stale states in StrategyEngine for recommendations that are no longer active
        stale_ids = set(self.strategy_engine._state.keys()) - active_rec_ids
        for rec_id in stale_ids:
            self.strategy_engine._state.pop(rec_id, None)

        log.info("✅ Trigger index rebuilt: %d items across %d unique asset/market pairs.", len(active_recs), len(new_triggers))

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
                symbol, market, low_price, high_price = await self.price_queue.get()
                
                # Delegate advanced strategy evaluation to the engine for each price in the tick
                with session_scope() as db_session:
                    # Find all relevant recommendations from the DB for this tick
                    recs_to_evaluate = self.repo.get_active_recs_for_asset_and_market(db_session, symbol, market)
                    for rec in recs_to_evaluate:
                        # Evaluate for both high and low price to catch wicks
                        await self.strategy_engine.evaluate_recommendation(db_session, rec, Decimal(str(high_price)), Decimal(str(low_price)))

                # Handle core triggers (Entry, SL, TP) using the in-memory index for speed
                trigger_key = f"{symbol.upper()}:{market}"
                await self.check_and_process_core_alerts(trigger_key, Decimal(str(low_price)), Decimal(str(high_price)))
                
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

    async def check_and_process_core_alerts(self, trigger_key: str, low_price: Decimal, high_price: Decimal):
        """Handles only the core triggers: Entry, SL, and TP."""
        async with self._triggers_lock:
            triggers_for_key = self.active_triggers.get(trigger_key, [])
        
        if not triggers_for_key: return

        processed_item_ids = set()
        for trigger in triggers_for_key:
            item_id = trigger["id"]
            if item_id in processed_item_ids: continue

            status = trigger["status"]
            side = trigger["side"]
            
            try:
                if status == RecommendationStatusEnum.PENDING:
                    entry_price, sl_price = trigger["entry"], trigger["stop_loss"]
                    if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        log.warning("Invalidation HIT for PENDING Rec #%s: SL hit before entry.", item_id)
                        await self.trade_service.process_invalidation_event(item_id)
                        processed_item_ids.add(item_id)
                    elif self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                        log.info("Activation HIT for PENDING Rec #%s.", item_id)
                        await self.trade_service.process_activation_event(item_id)
                        processed_item_ids.add(item_id)

                elif status == RecommendationStatusEnum.ACTIVE:
                    sl_price = trigger["stop_loss"]
                    if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        log.info("SL HIT for ACTIVE item #%s.", item_id)
                        await self.trade_service.process_sl_hit_event(item_id, sl_price)
                        processed_item_ids.add(item_id)
                        continue

                    for i, target in enumerate(trigger["targets"], 1):
                        if f"TP{i}_HIT" in trigger["processed_events"]: continue
                        target_price = Decimal(str(target['price']))
                        if self._is_price_condition_met(side, low_price, high_price, target_price, "TP"):
                            log.info("TP%d HIT for ACTIVE item #%s.", i, item_id)
                            await self.trade_service.process_tp_hit_event(item_id, i, target_price)
                            processed_item_ids.add(item_id)
            
            except Exception as e:
                log.error("Error processing core trigger for item #%s: %s", item_id, e, exc_info=True)
                processed_item_ids.add(item_id)