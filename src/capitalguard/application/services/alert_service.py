import logging
import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Set, Dict
from contextlib import contextmanager

from sqlalchemy.orm import Session, joinedload
from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy, OrderType
# ✅ FIX: Import the ORM model to be used with SQLAlchemy-specific functions like joinedload
from capitalguard.infrastructure.db.models import RecommendationORM
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
    ✅ FIXED: Systematic TP/SL detection failures for both LONG and SHORT positions
    ✅ FIXED: Removed problematic break statements for proper multi-target processing
    ✅ ENHANCED: Robust price comparison logic with proper float precision handling
    ✅ IMPROVED: Real-time recommendation state validation
    """
    
    def __init__(self, trade_service: TradeService, repo: RecommendationRepository):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue = asyncio.Queue()
        self.streamer = PriceStreamer(self.price_queue, self.repo)
        self._last_ws_update_time = None
        self._processing_task: asyncio.Task = None
        self._fallback_task: asyncio.Task = None
        self._recently_processed_events: Dict[str, datetime] = {}
        self._processing_lock = asyncio.Lock()

    async def _process_queue(self):  
        """Continuously processes price updates from the shared queue."""  
        log.info("AlertService queue processor started.")  
        while True:  
            try:  
                symbol, price = await self.price_queue.get()  
                self._last_ws_update_time = datetime.now(timezone.utc)  
                log.debug(f"Processing price from queue: {symbol} -> {price}")  
                
                async with self._processing_lock:
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
            
            current_time = datetime.now(timezone.utc)
            if self._recently_processed_events:
                expired_events = [
                    key for key, timestamp in self._recently_processed_events.items()
                    if current_time - timestamp > timedelta(minutes=5)
                ]
                for key in expired_events:
                    del self._recently_processed_events[key]
                if expired_events:
                    log.debug(f"Cleared {len(expired_events)} expired events from cache.")

            if self._last_ws_update_time is None or (current_time - self._last_ws_update_time) > timedelta(seconds=interval_seconds):  
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

    @contextmanager
    def _get_fresh_session(self):
        """Ensure fresh database session for each operation"""
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    async def check_and_process_alerts(self, specific_symbol: str = None, price_override: float = None):  
        """  
        Performs a check of open recommendations with enhanced reliability.
        """  
        with self._get_fresh_session() as db_session:
            try:  
                if specific_symbol:  
                    # ✅ CRITICAL FIX: Use RecommendationORM with joinedload, not the domain entity.
                    open_recs = self.repo.list_open_by_symbol(
                        db_session, specific_symbol, 
                        options=[joinedload(RecommendationORM.targets)]
                    )  
                else:  
                    # ✅ CRITICAL FIX: Use RecommendationORM with joinedload, not the domain entity.
                    open_recs = self.repo.list_open(
                        db_session, 
                        options=[joinedload(RecommendationORM.targets)]
                    )  
                  
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
                        db_session.refresh(rec)
                        await self._process_single_recommendation(db_session, rec, price, events_map.get(rec.id, set()))  
                  
                db_session.commit()  

            except Exception as e:  
                log.exception("Alert check loop failed internally, rolling back transaction: %s", e)  
                db_session.rollback()  

    def _is_price_condition_met(self, side: str, current_price: float, target_price: float, condition_type: str) -> bool:
        """
        Robust price comparison with proper float precision and side handling.
        """
        tolerance = 0.0001
        
        side_upper = side.upper()
        if side_upper == "LONG":
            if condition_type == "TP":
                return current_price >= (target_price - tolerance)
            elif condition_type == "SL":
                return current_price <= (target_price + tolerance)
            elif condition_type == "ENTRY_LIMIT":
                return current_price <= (target_price + tolerance)
            elif condition_type == "ENTRY_STOP":
                return current_price >= (target_price - tolerance)
                
        elif side_upper == "SHORT":
            if condition_type == "TP":
                return current_price <= (target_price + tolerance)
            elif condition_type == "SL":
                return current_price >= (target_price - tolerance)
            elif condition_type == "ENTRY_LIMIT":
                return current_price >= (target_price - tolerance)
            elif condition_type == "ENTRY_STOP":
                return current_price <= (target_price + tolerance)
        
        log.warning(f"Unknown condition type: {condition_type} for side: {side}")
        return False

    async def _process_single_recommendation(self, session: Session, rec: Recommendation, price: float, rec_events: Set[str]):  
        """  
        Comprehensive recommendation processing with systematic TP/SL detection.
        """  
        log.debug(f"Processing rec #{rec.id} ({rec.asset.value}) - Status: {rec.status}, Price: {price}")

        if rec.status == RecommendationStatus.PENDING:  
            event_key = f"{rec.id}:ACTIVATED"  
            if event_key in self._recently_processed_events: 
                log.debug(f"Skipping already processed activation for rec #{rec.id}")
                return  

            entry_price, side, order_type = rec.entry.value, rec.side.value, rec.order_type
            
            is_triggered = False
            if order_type == OrderType.LIMIT:
                is_triggered = self._is_price_condition_met(side, price, entry_price, "ENTRY_LIMIT")
            elif order_type == OrderType.STOP_MARKET:
                is_triggered = self._is_price_condition_met(side, price, entry_price, "ENTRY_STOP")
            
            if is_triggered:  
                self._recently_processed_events[event_key] = datetime.now(timezone.utc)
                log.info(f"ACTIVATING pending recommendation #{rec.id} for {rec.asset.value} at price {price}.")  
                try:
                    await self.trade_service.activate_recommendation_async(session, rec.id)  
                    session.commit()
                    log.info(f"Successfully activated recommendation #{rec.id}")
                except Exception as e:
                    log.error(f"Failed to activate recommendation #{rec.id}: {e}")
                    session.rollback()
                    self._recently_processed_events.pop(event_key, None)
            return  

        if rec.status == RecommendationStatus.ACTIVE:  
            self.repo.update_price_tracking(session, rec.id, price)  
            side, user_id = rec.side.value, rec.user_id  
            
            if not user_id: 
                log.warning(f"No user_id found for active recommendation #{rec.id}")
                return  

            sl_event_key = f"{rec.id}:SL_HIT"  
            if sl_event_key not in self._recently_processed_events and self._is_price_condition_met(side, price, rec.stop_loss.value, "SL"):
                self._recently_processed_events[sl_event_key] = datetime.now(timezone.utc)
                log.info(f"Auto-closing rec #{rec.id} due to SL hit at price {price}.")  
                try:
                    await self.trade_service.close_recommendation_for_user_async(session, rec.id, user_id, price, reason="SL_HIT")  
                    session.commit()
                    log.info(f"Successfully closed recommendation #{rec.id} due to SL")
                    return
                except Exception as e:
                    log.error(f"Failed to close recommendation #{rec.id} for SL: {e}")
                    session.rollback()
                    self._recently_processed_events.pop(sl_event_key, None)

            ps_event_key = f"{rec.id}:PROFIT_STOP_HIT"  
            if rec.profit_stop_price is not None and ps_event_key not in self._recently_processed_events and self._is_price_condition_met(side, price, rec.profit_stop_price, "SL"):
                self._recently_processed_events[ps_event_key] = datetime.now(timezone.utc)
                log.info(f"Auto-closing rec #{rec.id} due to Profit Stop hit at price {price}.")  
                try:
                    await self.trade_service.close_recommendation_for_user_async(session, rec.id, user_id, price, reason="PROFIT_STOP_HIT")  
                    session.commit()
                    log.info(f"Successfully closed recommendation #{rec.id} due to Profit Stop")
                    return
                except Exception as e:
                    log.error(f"Failed to close recommendation #{rec.id} for Profit Stop: {e}")
                    session.rollback()
                    self._recently_processed_events.pop(ps_event_key, None)

            final_tp_event_key = f"{rec.id}:FINAL_TP_HIT"  
            if (_env_bool("AUTO_CLOSE_ENABLED", False) and rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP and 
                rec.targets.values and final_tp_event_key not in self._recently_processed_events):
                
                last_tp_price = rec.targets.values[-1].price  
                if self._is_price_condition_met(side, price, last_tp_price, "TP"):
                    self._recently_processed_events[final_tp_event_key] = datetime.now(timezone.utc)
                    log.info(f"Auto-closing rec #{rec.id} due to final TP hit at price {price}.")  
                    try:
                        await self.trade_service.close_recommendation_for_user_async(session, rec.id, user_id, price, reason="FINAL_TP_HIT")  
                        session.commit()
                        log.info(f"Successfully closed recommendation #{rec.id} due to Final TP")
                        return
                    except Exception as e:
                        log.error(f"Failed to close recommendation #{rec.id} for Final TP: {e}")
                        session.rollback()
                        self._recently_processed_events.pop(final_tp_event_key, None)
              
            if rec.targets.values:  
                for i, target in enumerate(rec.targets.values):  
                    tp_event_key = f"{rec.id}:TP{i+1}_HIT"  
                    if tp_event_key in self._recently_processed_events or f"TP{i+1}_HIT" in rec_events: 
                        continue  
                      
                    if self._is_price_condition_met(side, price, target.price, "TP"):  
                        self._recently_processed_events[tp_event_key] = datetime.now(timezone.utc)
                        log.info(f"TP{i+1} hit for rec #{rec.id} at price {price}. Target: {target.price}")  
                        try:
                            await self.trade_service.process_target_hit_async(session, rec.id, user_id, i + 1, price)  
                            session.commit()
                            log.info(f"Successfully processed TP{i+1} for recommendation #{rec.id}")
                        except Exception as e:
                            log.error(f"Failed to process TP{i+1} for recommendation #{rec.id}: {e}")
                            session.rollback()
                            self._recently_processed_events.pop(tp_event_key, None)

    async def force_recheck_recommendation(self, rec_id: int):
        """
        Force a recheck of specific recommendation (for manual testing/debugging).
        """
        with self._get_fresh_session() as session:
            try:
                rec = self.repo.get_by_id(session, rec_id, options=[joinedload(RecommendationORM.targets)])
                if not rec:
                    log.warning(f"Recommendation #{rec.id} not found for forced recheck")
                    return
                
                price_map = await asyncio.get_running_loop().run_in_executor(None, BinancePricing.get_all_prices, False)
                price = price_map.get(rec.asset.value)
                if price is None:
                    log.warning(f"Could not get price for {rec.asset.value} during forced recheck")
                    return
                
                rec_events = self.repo.get_events_for_recommendations(session, [rec_id]).get(rec_id, set())
                
                log.info(f"Force rechecking rec #{rec_id} at price {price}")
                await self._process_single_recommendation(session, rec, price, rec_events)
                session.commit()
                
            except Exception as e:
                log.error(f"Error during forced recheck of rec #{rec.id}: {e}")
                session.rollback()

    async def get_processing_stats(self) -> Dict:
        """
        Get current processing statistics for monitoring.
        """
        return {
            "queue_size": self.price_queue.qsize(),
            "recently_processed_events_count": len(self._recently_processed_events),
            "last_ws_update": self._last_ws_update_time,
            "processing_task_running": self._processing_task is not None and not self._processing_task.done(),
            "fallback_task_running": self._fallback_task is not None and not self._fallback_task.done(),
        }