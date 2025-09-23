# --- START OF FINAL, HYBRID, AND ROBUST FILE (Version 13.2.2) ---
# src/capitalguard/application/services/alert_service.py
import logging
import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Set

from sqlalchemy.orm import Session
from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy, OrderType
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer

log = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else default

class AlertService:
    """
    The central brain for processing all price-driven events.
    It consumes prices from a high-speed queue and uses a periodic REST API call as a safety fallback.
    ✅ It now includes an in-memory lock to prevent alert storms from race conditions.
    """
    
    def __init__(self, trade_service: TradeService, repo: RecommendationRepository):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue = asyncio.Queue()
        self.streamer = PriceStreamer(self.price_queue, self.repo)
        self._last_ws_update_time = None
        self._processing_task: asyncio.Task = None
        self._fallback_task: asyncio.Task = None
        # ✅ NEW: In-memory set to act as a short-term lock for recently processed events.
        self._recently_processed_events: Set[str] = set()

    async def _process_queue(self):  
        """Continuously processes price updates from the shared queue."""  
        log.info("AlertService queue processor started.")  
        while True:  
            try:  
                symbol, price = await self.price_queue.get()  
                self._last_ws_update_time = datetime.now(timezone.utc)  
                log.debug(f"Processing price from queue: {symbol} -> {price}")  
                await self.check_and_process_alerts(specific_symbol=symbol, price_override=price)  
                self.price_queue.task_done()  
            except (asyncio.CancelledError, KeyboardInterrupt):  
                log.info("Queue processor task cancelled.")  
                break  
            except Exception:  
                log.exception("Unhandled exception in queue processor.")  

    async def _run_fallback_timer(self, interval_seconds: int = 30):  
        """Periodically checks if the WebSocket is alive and triggers a REST fallback if not."""  
        log.info(f"Safety fallback timer started. Will check every {interval_seconds}s.")  
        while True:  
            await asyncio.sleep(interval_seconds)  
              
            # Clear the in-memory lock periodically to allow re-processing if needed after some time.  
            if self._recently_processed_events:  
                log.debug(f"Clearing {len(self._recently_processed_events)} items from the processed events cache.")  
                self._recently_processed_events.clear()  

            if self._last_ws_update_time is None or (datetime.now(timezone.utc) - self._last_ws_update_time) > timedelta(seconds=interval_seconds):  
                log.warning("WebSocket stream seems stale. Triggering REST-based fallback check.")  
                await self.check_and_process_alerts()  

    def start(self):  
        """Starts the streamer, queue processor, and fallback timer as background tasks."""  
        self.streamer.start()  
        if self._processing_task is None or self._processing_task.done():  
            self._processing_task = asyncio.create_task(self._process_queue())  
            self._fallback_task = asyncio.create_task(self._run_fallback_timer())  
        else:  
            log.warning("AlertService processing tasks are already running.")  

    def stop(self):  
        """Stops all background tasks."""  
        self.streamer.stop()  
        if self._processing_task and not self._processing_task.done():  
            self._processing_task.cancel()  
        if self._fallback_task and not self._fallback_task.done():  
            self._fallback_task.cancel()  
        self._processing_task = None  
        self._fallback_task = None  
        log.info("Hybrid AlertService stopped.")  

    async def check_and_process_alerts(self, specific_symbol: str = None, price_override: float = None):  
        """  
        Performs a check of open recommendations. Can be triggered for all symbols (fallback)  
        or a specific symbol (queue).  
        """  
        with SessionLocal() as db_session:  
            try:  
                if specific_symbol:  
                    open_recs = self.repo.list_open_by_symbol(db_session, specific_symbol)  
                else:  
                    open_recs = self.repo.list_open(db_session)  
                  
                if not open_recs:  
                    return  

                price_map = {}  
                if price_override and specific_symbol:  
                    price_map[specific_symbol] = price_override  
                else:  
                    loop = asyncio.get_running_loop()  
                    price_map = await loop.run_in_executor(None, BinancePricing.get_all_prices, False)  

                if not price_map:  
                    log.warning("Could not fetch prices for alert check.")  
                    return  
                  
                rec_ids = [rec.id for rec in open_recs]  
                events_map = self.repo.get_events_for_recommendations(db_session, rec_ids)  

                for rec in open_recs:  
                    price = price_map.get(rec.asset.value)  
                    if price is not None:  
                        await self._process_single_recommendation(db_session, rec, price, events_map.get(rec.id, set()))  
                  
                db_session.commit()  

            except Exception as e:  
                log.exception("Alert check loop failed internally, rolling back transaction: %s", e)  
                db_session.rollback()  

    async def _process_single_recommendation(self, session: Session, rec: Recommendation, price: float, rec_events: Set[str]):  
        """  
        Processes all logic for a single recommendation, protected by an in-memory lock.  
        """  
        # --- Step 1: Handle PENDING recommendations (Activation Logic) ---  
        if rec.status == RecommendationStatus.PENDING:  
            event_key = f"{rec.id}:ACTIVATED"  
            if event_key in self._recently_processed_events: 
                return  

            entry, side, order_type = rec.entry.value, rec.side.value, rec.order_type  
            is_triggered = False  
            if order_type == OrderType.LIMIT and ((side == "LONG" and price <= entry) or (side == "SHORT" and price >= entry)):  
                is_triggered = True  
            elif order_type == OrderType.STOP_MARKET and ((side == "LONG" and price >= entry) or (side == "SHORT" and price <= entry)):  
                is_triggered = True  
              
            if is_triggered:  
                self._recently_processed_events.add(event_key)  
                log.info(f"ACTIVATING pending recommendation #{rec.id} for {rec.asset.value} at price {price}.")  
                await self.trade_service.activate_recommendation_async(session, rec.id)  
            return  

        # --- Step 2: Handle ACTIVE recommendations (Monitoring Logic) ---  
        if rec.status == RecommendationStatus.ACTIVE:  
            self.repo.update_price_tracking(session, rec.id, price)  
            side = rec.side.value.upper()  
            user_id = rec.user_id  
            if not user_id: 
                return  

            # Check for Stop Loss  
            sl_event_key = f"{rec.id}:SL_HIT"  
            if sl_event_key not in self._recently_processed_events and ((side == "LONG" and price <= rec.stop_loss.value) or (side == "SHORT" and price >= rec.stop_loss.value)):  
                self._recently_processed_events.add(sl_event_key)  
                log.info(f"Auto-closing rec #{rec.id} due to SL hit at price {price}.")  
                await self.trade_service.close_recommendation_for_user_async(session, rec.id, user_id, price, reason="SL_HIT")  
                return  

            # Check for Profit Stop  
            ps_event_key = f"{rec.id}:PROFIT_STOP_HIT"  
            if ps_event_key not in self._recently_processed_events and rec.profit_stop_price is not None and ((side == "LONG" and price <= rec.profit_stop_price) or (side == "SHORT" and price >= rec.profit_stop_price)):  
                self._recently_processed_events.add(ps_event_key)  
                log.info(f"Auto-closing rec #{rec.id} due to Profit Stop hit at price {price}.")  
                await self.trade_service.close_recommendation_for_user_async(session, rec.id, user_id, price, reason="PROFIT_STOP_HIT")  
                return  

            # Check for Final TP auto-close  
            final_tp_event_key = f"{rec.id}:FINAL_TP_HIT"  
            auto_close_enabled = _env_bool("AUTO_CLOSE_ENABLED", False)  
            if final_tp_event_key not in self._recently_processed_events and auto_close_enabled and rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP and rec.targets.values:  
                last_tp_price = rec.targets.values[-1].price  
                if (side == "LONG" and price >= last_tp_price) or (side == "SHORT" and price <= last_tp_price):  
                    self._recently_processed_events.add(final_tp_event_key)  
                    log.info(f"Auto-closing rec #{rec.id} due to final TP hit at price {price}.")  
                    await self.trade_service.close_recommendation_for_user_async(session, rec.id, user_id, price, reason="FINAL_TP_HIT")  
                    return  
              
            # Check for individual TP hits  
            if rec.targets.values:  
                for i, target in enumerate(rec.targets.values):  
                    tp_event_key = f"{rec.id}:TP{i+1}_HIT"  
                    if tp_event_key in self._recently_processed_events: 
                        continue  
                      
                    event_type_hit = f"TP{i+1}_HIT"  
                    if event_type_hit not in rec_events:  
                        if (side == "LONG" and price >= target.price) or (side == "SHORT" and price <= target.price):  
                            self._recently_processed_events.add(tp_event_key)  
                            log.info(f"TP{i+1} hit for rec #{rec.id}. Processing.")  
                            await self.trade_service.process_target_hit_async(session, rec.id, user_id, i + 1, price)  
                           