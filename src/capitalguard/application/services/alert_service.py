#___ src/capitalguard/application/services/alert_service.py
# --- START OF RE-ARCHITECTED AND CORRECTED FILE (Version 14.0.0) ---
import logging
import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Set, Dict
from contextlib import contextmanager

from sqlalchemy.orm import Session
from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy, OrderType
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
    ✅ RE-ARCHITECTED: Logic flow is now event-driven, prioritizing partial profits
    before final closing conditions to prevent missed notifications.
    ✅ ENHANCED: State is refreshed from DB after partial profits to ensure
    subsequent checks (like SL) use the most up-to-date data.
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
        self.streamer.start()  
        if self._processing_task is None or self._processing_task.done():  
            self._processing_task = asyncio.create_task(self._process_queue())  
            self._fallback_task = asyncio.create_task(self._run_fallback_timer())  
        else:  
            log.warning("AlertService processing tasks are already running.")  

    def stop(self):  
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
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    async def check_and_process_alerts(self, specific_symbol: str = None, price_override: float = None):  
        with self._get_fresh_session() as db_session:
            try:  
                if specific_symbol:  
                    open_recs_orm = self.repo.list_open_by_symbol_orm(db_session, specific_symbol)
                else:  
                    open_recs_orm = self.repo.list_open_orm(db_session)
                  
                if not open_recs_orm:  
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
                  
                rec_ids = [rec.id for rec in open_recs_orm]  
                events_map = self.repo.get_events_for_recommendations(db_session, rec_ids)  

                for rec_orm in open_recs_orm:  
                    price = price_map.get(rec_orm.asset)  
                    if price is not None:  
                        await self._process_single_recommendation(db_session, rec_orm, price, events_map.get(rec_orm.id, set()))
                  
            except Exception as e:  
                log.exception("Alert check loop failed internally, rolling back transaction: %s", e)  
                db_session.rollback()  

    def _is_price_condition_met(self, side: str, current_price: float, target_price: float, condition_type: str) -> bool:
        tolerance = 0.0001
        side_upper = side.upper()
        if side_upper == "LONG":
            if condition_type == "TP": return current_price >= (target_price - tolerance)
            elif condition_type == "SL": return current_price <= (target_price + tolerance)
            elif condition_type == "ENTRY_LIMIT": return current_price <= (target_price + tolerance)
            elif condition_type == "ENTRY_STOP": return current_price >= (target_price - tolerance)
        elif side_upper == "SHORT":
            if condition_type == "TP": return current_price <= (target_price + tolerance)
            elif condition_type == "SL": return current_price >= (target_price - tolerance)
            elif condition_type == "ENTRY_LIMIT": return current_price >= (target_price - tolerance)
            elif condition_type == "ENTRY_STOP": return current_price <= (target_price + tolerance)
        log.warning(f"Unknown condition type: {condition_type} for side: {side}")
        return False

    async def _process_single_recommendation(self, session: Session, rec_orm: RecommendationORM, price: float, rec_events: Set[str]):  
        rec = self.repo._to_entity(rec_orm)
        if not rec: return

        log.debug(f"Processing rec #{rec.id} ({rec.asset.value}) - Status: {rec.status}, Price: {price}")
        
        # --- 1. Handle PENDING recommendations (Activation Logic) ---
        if rec.status == RecommendationStatus.PENDING:  
            event_key = f"{rec.id}:ACTIVATED"  
            if event_key in self._recently_processed_events: return
            
            entry_price, side, order_type = rec.entry.value, rec.side.value, rec.order_type
            is_triggered = False
            if order_type == OrderType.LIMIT: is_triggered = self._is_price_condition_met(side, price, entry_price, "ENTRY_LIMIT")
            elif order_type == OrderType.STOP_MARKET: is_triggered = self._is_price_condition_met(side, price, entry_price, "ENTRY_STOP")
            
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

        # --- 2. Handle ACTIVE recommendations ---
        if rec.status == RecommendationStatus.ACTIVE:  
            self.repo.update_price_tracking(session, rec.id, price)  
            side, user_id = rec.side.value, rec.user_id  
            if not user_id: return

            # ✅ STEP 1: Process all intermediate targets first (highest priority)
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
                            session.commit() # Commit after each successful TP processing
                            log.info(f"Successfully processed TP{i+1} for recommendation #{rec.id}")
                        except Exception as e:
                            log.error(f"Failed to process TP{i+1} for recommendation #{rec.id}: {e}")
                            session.rollback()
                            self._recently_processed_events.pop(tp_event_key, None)
            
            # ✅ STEP 2: Refresh state from DB to see if partial profits closed the trade
            session.refresh(rec_orm)
            rec = self.repo._to_entity(rec_orm)
            if not rec or rec.status == RecommendationStatus.CLOSED:
                log.info(f"Recommendation #{rec.id} is now closed. Ending processing cycle.")
                return

            # ✅ STEP 3: Now, check for trade-ending conditions on the remaining position
            # 3.1 Check Stop Loss
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

            # 3.2 Check Profit Stop
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

            # 3.3 Check Final TP Auto-Close Strategy
            final_tp_event_key = f"{rec.id}:FINAL_TP_HIT"  
            if (_env_bool("AUTO_CLOSE_ENABLED", False) and rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP and 
                rec.targets.values and final_tp_event_key not in self._recently_processed_events):
                
                last_tp_price = rec.targets.values[-1].price  
                if self._is_price_condition_met(side, price, last_tp_price, "TP"):
                    self._recently_processed_events[final_tp_event_key] = datetime.now(timezone.utc)
                    log.info(f"Auto-closing remaining position for rec #{rec.id} due to final TP hit at price {price}.")  
                    try:
                        await self.trade_service.close_recommendation_for_user_async(session, rec.id, user_id, price, reason="FINAL_TP_HIT")  
                        session.commit()
                        log.info(f"Successfully closed recommendation #{rec.id} due to Final TP")
                        return
                    except Exception as e:
                        log.error(f"Failed to close recommendation #{rec.id} for Final TP: {e}")
                        session.rollback()
                        self._recently_processed_events.pop(final_tp_event_key, None)

# --- END OF RE-ARCHITECTED AND CORRECTED FILE (Version 14.0.0) ---