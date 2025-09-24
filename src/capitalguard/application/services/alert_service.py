# --- START OF FINAL, COMPLETE, AND PRODUCTION-READY FILE (Version 18.3.0) ---
# src/capitalguard/application/services/alert_service.py

import logging
import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from contextlib import contextmanager

from sqlalchemy.orm import Session
from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy, OrderType
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer
from capitalguard.interfaces.telegram.ui_texts import _pct

if TYPE_CHECKING:
    from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else default

class AlertService:
    """
    The central brain for processing all price-driven events, architected for massive scale.
    ✅ FINAL ARCHITECTURE v18.3: Hardened logic for trigger management, price comparison,
    and task creation to ensure stability and reliability.
    """
    
    def __init__(self, trade_service: 'TradeService', repo: RecommendationRepository):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue = asyncio.Queue()
        self.streamer = PriceStreamer(self.price_queue, self.repo)
        
        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()

        self._processing_task: asyncio.Task = None
        self._index_sync_task: asyncio.Task = None

    async def build_triggers_index(self):
        log.info("Building in-memory trigger index for all active recommendations...")
        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        with SessionLocal() as session:
            trigger_data = self.repo.list_all_active_triggers_data(session)
        for item in trigger_data:
            self._add_item_to_trigger_dict(new_triggers, item)
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        log.info(f"Successfully built trigger index with {len(trigger_data)} recommendations across {len(new_triggers)} symbols.")

    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        asset = (item['asset'] or "").strip().upper()
        if not asset: return
        if asset not in trigger_dict:
            trigger_dict[asset] = []
        if item['status'] == RecommendationStatus.PENDING:
            trigger_dict[asset].append({
                "rec_id": item['id'], "user_id": item['user_id'], "side": item['side'],
                "type": "ENTRY", "price": item['entry'], "order_type": item['order_type']
            })
        elif item['status'] == RecommendationStatus.ACTIVE:
            trigger_dict[asset].append({
                "rec_id": item['id'], "user_id": item['user_id'], "side": item['side'],
                "type": "SL", "price": item['stop_loss']
            })
            if item.get('profit_stop_price'):
                trigger_dict[asset].append({
                    "rec_id": item['id'], "user_id": item['user_id'], "side": item['side'],
                    "type": "PROFIT_STOP", "price": item['profit_stop_price']
                })
            for i, target in enumerate(item['targets']):
                trigger_dict[asset].append({
                    "rec_id": item['id'], "user_id": item['user_id'], "side": item['side'],
                    "type": f"TP{i+1}", "price": target['price']
                })

    async def update_triggers_for_recommendation(self, rec_id: int):
        log.debug(f"Attempting to update triggers for Rec #{rec_id} in memory.")
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t['rec_id'] != rec_id]
                if not self.active_triggers[symbol]:
                    del self.active_triggers[symbol]
            with SessionLocal() as session:
                item = self.repo.get_active_trigger_data_by_id(session, rec_id)
            if item:
                self._add_item_to_trigger_dict(self.active_triggers, item)
                log.info(f"Successfully updated triggers for Rec #{rec_id} in memory.")

    async def remove_triggers_for_recommendation(self, rec_id: int):
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                original_count = len(self.active_triggers[symbol])
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t['rec_id'] != rec_id]
                if len(self.active_triggers[symbol]) < original_count:
                    log.info(f"Removed triggers for Rec #{rec_id} from symbol {symbol} in memory.")
                    if not self.active_triggers[symbol]:
                        del self.active_triggers[symbol]
                    break

    async def _run_index_sync(self, interval_seconds: int = 300):
        log.info(f"Index background synchronization task started. Syncing every {interval_seconds}s.")
        while True:
            await asyncio.sleep(interval_seconds)
            await self.build_triggers_index()

    async def _process_queue(self):
        log.info("AlertService queue processor started.")
        while True:
            try:
                symbol, low_price, high_price = await self.price_queue.get()
                await self.check_and_process_alerts(symbol, low_price, high_price)
                self.price_queue.task_done()
            except (asyncio.CancelledError, KeyboardInterrupt):
                log.info("Queue processor task cancelled.")
                break
            except Exception:
                log.exception("Unhandled exception in queue processor.")

    def start(self):
        try:
            loop = asyncio.get_running_loop()
            if self._processing_task is None or self._processing_task.done():
                self._processing_task = loop.create_task(self._process_queue())
                self._index_sync_task = loop.create_task(self._run_index_sync())
            else:
                log.warning("AlertService processing tasks are already running.")
            self.streamer.start()
        except RuntimeError:
            log.error("AlertService.start() called without a running event loop.")

    def stop(self):
        self.streamer.stop()
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
        if self._index_sync_task and not self._index_sync_task.done():
            self._index_sync_task.cancel()
        self._processing_task = None
        self._index_sync_task = None
        log.info("High-performance AlertService stopped.")

    def _is_price_condition_met(self, side: str, low_price: float, high_price: float, target_price: float, condition_type: str, order_type: Optional[OrderType] = None) -> bool:
        side_upper = side.upper()
        if side_upper == "LONG":
            if condition_type.startswith("TP"): return high_price >= target_price
            if condition_type in ("SL", "PROFIT_STOP"): return low_price <= target_price
            if condition_type == "ENTRY":
                if order_type == OrderType.LIMIT: return low_price <= target_price
                if order_type == OrderType.STOP_MARKET: return high_price >= target_price
        elif side_upper == "SHORT":
            if condition_type.startswith("TP"): return low_price <= target_price
            if condition_type in ("SL", "PROFIT_STOP"): return high_price >= target_price
            if condition_type == "ENTRY":
                if order_type == OrderType.LIMIT: return high_price >= target_price
                if order_type == OrderType.STOP_MARKET: return low_price <= target_price
        return False

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        async with self._triggers_lock:
            triggers_for_symbol = self.active_triggers.get(symbol.upper(), [])
        
        if not triggers_for_symbol:
            return

        triggered_ids = set()
        for trigger in triggers_for_symbol:
            execution_price = trigger['price']
            if self._is_price_condition_met(trigger['side'], low_price, high_price, execution_price, trigger['type'], trigger.get('order_type')):
                if trigger['rec_id'] in triggered_ids: continue
                
                log.info(f"Trigger HIT for Rec #{trigger['rec_id']}: Type={trigger['type']}, Price Range=[{low_price}, {high_price}], Trigger Price={execution_price}")
                triggered_ids.add(trigger['rec_id'])
                
                try:
                    if trigger['type'] == 'ENTRY':
                        await self.trade_service.process_activation_event(trigger['rec_id'])
                    elif trigger['type'].startswith('TP'):
                        target_index = int(trigger['type'][2:])
                        await self.trade_service.process_tp_hit_event(trigger['rec_id'], trigger['user_id'], target_index, execution_price)
                    elif trigger['type'] == 'SL':
                        await self.trade_service.process_sl_hit_event(trigger['rec_id'], trigger['user_id'], execution_price)
                    elif trigger['type'] == 'PROFIT_STOP':
                        await self.trade_service.process_profit_stop_hit_event(trigger['rec_id'], trigger['user_id'], execution_price)
                except Exception as e:
                    log.error(f"Failed to process event for recommendation #{trigger['rec_id']}: {e}", exc_info=True)

# --- END OF FINAL, COMPLETE, AND PRODUCTION-READY FILE (Version 18.3.0) ---