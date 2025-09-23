# --- START OF FINAL, PRODUCTION-READY FILE (Version 17.0.0) ---
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
from capitalguard.interfaces.telegram.ui_texts import _pct

log = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else default

class AlertService:
    """
    The central brain for processing all price-driven events.
    ✅ FINAL ARCHITECTURE v17: Each recommendation is now fetched with its full, fresh
    state at the beginning of its processing cycle. This guarantees that the service
    always acts on the most current data, preventing duplicate event processing (e.g.,
    repeated TP notifications) caused by stale state.
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
        """Provides a temporary session for read-only operations within this service."""
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    async def check_and_process_alerts(self, specific_symbol: str = None, price_override: float = None):  
        open_recs_stubs = []
        with self._get_fresh_session() as db_session:
            if specific_symbol:  
                open_recs_stubs = self.repo.list_open_by_symbol_orm(db_session, specific_symbol)
            else:  
                open_recs_stubs = self.repo.list_open_orm(db_session)
        
        if not open_recs_stubs: return  

        price_map = {}  
        if price_override and specific_symbol:  
            price_map = {specific_symbol: price_override}
        else:  
            loop = asyncio.get_running_loop()  
            price_map = await loop.run_in_executor(None, BinancePricing.get_all_prices, False)  

        if not price_map: return  
        
        for rec_stub in open_recs_stubs:  
            price = price_map.get(rec_stub.asset)  
            if price is not None:
                try:
                    await self._process_single_recommendation(rec_stub.id, price)
                except Exception as e:
                    log.error(f"Failed to process recommendation #{rec_stub.id}: {e}", exc_info=True)

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

    async def _process_single_recommendation(self, rec_id: int, price: float):
        rec = None
        with self._get_fresh_session() as session:
            rec = self.repo.get(session, rec_id)

        if not rec:
            log.warning(f"Recommendation #{rec_id} not found during processing, might have been closed or deleted.")
            return

        rec_events = {event.event_type for event in (rec.events or [])}
        log.debug(f"Processing rec #{rec.id} ({rec.asset.value}) - Status: {rec.status}, Price: {price}")
        
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
                    updated_rec = await self.trade_service.activate_recommendation_async(rec.id)
                    if updated_rec:
                        self.trade_service.notify_reply(rec.id, f"▶️ **Trade Activated** | **{rec.asset.value}** entry price has been reached.")
                        await self.trade_service.notify_card_update(updated_rec)
                except Exception as e:
                    log.error(f"Activation transaction failed for rec #{rec.id}: {e}")
                    self._recently_processed_events.pop(event_key, None)
            return  

        if rec.status == RecommendationStatus.ACTIVE:  
            side, user_id = rec.side.value, rec.user_id  
            if not user_id: return

            with self._get_fresh_session() as session:
                self.repo.update_price_tracking(session, rec.id, price)
                session.commit()

            # --- Event Detection and Post-Transaction Notification ---
            
            if rec.targets.values:  
                for i, target in enumerate(rec.targets.values):  
                    tp_event_key = f"{rec.id}:TP{i+1}_HIT"  
                    if tp_event_key in self._recently_processed_events or f"TP{i+1}_HIT" in rec_events: 
                        continue  
                    
                    if self._is_price_condition_met(side, price, target.price, "TP"):  
                        self._recently_processed_events[tp_event_key] = datetime.now(timezone.utc)
                        log.info(f"Detected TP{i+1} hit for rec #{rec.id}. Delegating to TradeService.")  
                        try:
                            updated_rec = await self.trade_service.process_target_hit_async(rec.id, user_id, i + 1, price)
                            self.trade_service.notify_reply(rec.id, f"🔥 **Target {i+1} Hit!** | **{rec.asset.value}** reached **{target.price:g}**.")
                            if target.close_percent > 0:
                                pnl_on_part = _pct(updated_rec.entry.value, price, side)
                                notification_text = (f"💰 **Partial Profit Taken** | Signal #{rec.id}\n\n"
                                                   f"Closed **{target.close_percent:.2f}%** of **{rec.asset.value}** at **{price:g}** for a **{pnl_on_part:+.2f}%** profit.\n\n"
                                                   f"<i>Remaining open size: {updated_rec.open_size_percent:.2f}%</i>")
                                self.trade_service.notify_reply(rec.id, notification_text)
                            await self.trade_service.notify_card_update(updated_rec)
                        except Exception as e:
                            log.error(f"TP{i+1} processing transaction failed for rec #{rec.id}: {e}")
                            self._recently_processed_events.pop(tp_event_key, None)

            sl_event_key = f"{rec.id}:SL_HIT"  
            if sl_event_key not in self._recently_processed_events and self._is_price_condition_met(side, price, rec.stop_loss.value, "SL"):
                self._recently_processed_events[sl_event_key] = datetime.now(timezone.utc)
                log.info(f"Detected SL hit for rec #{rec.id}. Delegating to TradeService.")  
                try:
                    updated_rec = await self.trade_service.close_recommendation_for_user_async(rec.id, user_id, price, reason="SL_HIT")
                    if updated_rec:
                        pnl = _pct(updated_rec.entry.value, price, side)
                        emoji, r_text = ("🏆", "Profit") if pnl > 0.001 else ("💔", "Loss")
                        self.trade_service.notify_reply(rec.id, f"<b>{emoji} Trade Closed #{updated_rec.asset.value}</b>\nClosed at {price:g} for a result of <b>{pnl:+.2f}%</b> ({r_text}).")
                        await self.trade_service.notify_card_update(updated_rec)
                except Exception as e:
                    log.error(f"SL closing transaction failed for rec #{rec.id}: {e}")
                    self._recently_processed_events.pop(sl_event_key, None)

            ps_event_key = f"{rec.id}:PROFIT_STOP_HIT"  
            if rec.profit_stop_price is not None and ps_event_key not in self._recently_processed_events and self._is_price_condition_met(side, price, rec.profit_stop_price, "SL"):
                self._recently_processed_events[ps_event_key] = datetime.now(timezone.utc)
                log.info(f"Detected Profit Stop hit for rec #{rec.id}. Delegating to TradeService.")  
                try:
                    updated_rec = await self.trade_service.close_recommendation_for_user_async(rec.id, user_id, price, reason="PROFIT_STOP_HIT")
                    if updated_rec:
                        pnl = _pct(updated_rec.entry.value, price, side)
                        emoji, r_text = ("🏆", "Profit") if pnl > 0.001 else ("💔", "Loss")
                        self.trade_service.notify_reply(rec.id, f"<b>{emoji} Trade Closed #{updated_rec.asset.value}</b>\nClosed at {price:g} for a result of <b>{pnl:+.2f}%</b> ({r_text}).")
                        await self.trade_service.notify_card_update(updated_rec)
                except Exception as e:
                    log.error(f"Profit Stop closing transaction failed for rec #{rec.id}: {e}")
                    self._recently_processed_events.pop(ps_event_key, None)

            final_tp_event_key = f"{rec.id}:FINAL_TP_HIT"  
            if (_env_bool("AUTO_CLOSE_ENABLED", False) and rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP and 
                rec.targets.values and final_tp_event_key not in self._recently_processed_events):
                
                last_tp_price = rec.targets.values[-1].price  
                if self._is_price_condition_met(side, price, last_tp_price, "TP"):
                    self._recently_processed_events[final_tp_event_key] = datetime.now(timezone.utc)
                    log.info(f"Detected final TP auto-close for rec #{rec.id}. Delegating to TradeService.")  
                    try:
                        updated_rec = await self.trade_service.close_recommendation_for_user_async(rec.id, user_id, price, reason="FINAL_TP_HIT")
                        if updated_rec:
                            pnl = _pct(updated_rec.entry.value, price, side)
                            emoji, r_text = ("🏆", "Profit") if pnl > 0.001 else ("💔", "Loss")
                            self.trade_service.notify_reply(rec.id, f"<b>{emoji} Trade Closed #{updated_rec.asset.value}</b>\nClosed at {price:g} for a result of <b>{pnl:+.2f}%</b> ({r_text}).")
                            await self.trade_service.notify_card_update(updated_rec)
                    except Exception as e:
                        log.error(f"Final TP closing transaction failed for rec #{rec.id}: {e}")
                        self._recently_processed_events.pop(final_tp_event_key, None)

# --- END OF FINAL, PRODUCTION-READY FILE (Version 17.0.0) ---