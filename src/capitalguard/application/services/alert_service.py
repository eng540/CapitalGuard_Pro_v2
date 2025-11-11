# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---
# src/capitalguard/application/services/alert_service.py (v27.2 - NameError Hotfix)
"""
AlertService - Orchestrates price updates and manages the in-memory trigger index.
✅ THE FIX (ADR-001): Implemented "Smart Indexing" (Append-Only Logic).
    - Added `add_trigger_data` and `remove_single_trigger` to allow TradeService
      to update the in-memory index instantly without a full database rebuild.
    - Added `build_trigger_data_from_orm` helper to build the required
      dictionary structure, ensuring consistency with the main `build_triggers_index`.
✅ HOTFIX (v27.1): Modified `_process_queue` to pass `rebuild_alerts=False`
    during automated closures (SL/TP hits), preventing redundant full-index rebuilds.
✅ HOTFIX (v27.2): Added missing import for `PriceStreamer` to resolve
    the `NameError: name 'PriceStreamer' is not defined` crash loop.
"""

import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional, Union
from decimal import Decimal, InvalidOperation
import time
from datetime import datetime, timezone

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.db.models import (
    Recommendation, UserTrade,
    RecommendationStatusEnum, UserTradeStatusEnum, OrderTypeEnum
)
from capitalguard.application.strategy.engine import StrategyEngine, BaseAction, CloseAction, MoveSLAction
# ✅ HOTFIX (v27.2): Added the missing import for PriceStreamer
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer


if False:
    from .trade_service import TradeService
    from .price_service import PriceService

log = logging.getLogger(__name__)

# --- Helper Function (copied from trade_service) ---
def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """Safely converts input to a Decimal."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    if value is None:
        return default
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError):
        log.debug(f"AlertService: Could not convert '{value}' to Decimal.")
        return default

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

    # --- ✅ NEW (ADR-001): Helper to build trigger dict from ORM ---
    def build_trigger_data_from_orm(self, item_orm: Union[Recommendation, UserTrade]) -> Optional[Dict[str, Any]]:
        """
        Builds the standard trigger dictionary from a *single* ORM object.
        This logic is mirrored from RecommendationRepository.list_all_active_triggers_data.
        """
        try:
            if isinstance(item_orm, Recommendation):
                rec = item_orm
                entry_dec = _to_decimal(rec.entry)
                sl_dec = _to_decimal(rec.stop_loss)
                targets_list = [
                    {"price": _to_decimal(t.get("price")), "close_percent": t.get("close_percent", 0.0)}
                    for t in (rec.targets or []) if t.get("price") is not None
                ]
                user = getattr(rec, 'analyst', None)
                if not user:
                    log.warning(f"Skipping trigger build for Rec ID {rec.id}: Analyst relationship not loaded.")
                    return None
                
                return {
                    "id": rec.id,
                    "item_type": "recommendation",
                    "user_id": str(user.telegram_user_id),
                    "user_db_id": rec.analyst_id,
                    "asset": rec.asset,
                    "side": rec.side,
                    "entry": entry_dec,
                    "stop_loss": sl_dec,
                    "targets": targets_list,
                    "status": rec.status,
                    "order_type": rec.order_type,
                    "market": rec.market,
                    "processed_events": {e.event_type for e in (getattr(rec, 'events', []) or [])},
                    "profit_stop_mode": getattr(rec, 'profit_stop_mode', 'NONE'),
                    "profit_stop_price": _to_decimal(getattr(rec, 'profit_stop_price', None)) if getattr(rec, 'profit_stop_price', None) is not None else None,
                    "profit_stop_trailing_value": _to_decimal(getattr(rec, 'profit_stop_trailing_value', None)) if getattr(rec, 'profit_stop_trailing_value', None) is not None else None,
                    "profit_stop_active": getattr(rec, 'profit_stop_active', False),
                    "original_published_at": None,
                }
            
            elif isinstance(item_orm, UserTrade):
                trade = item_orm
                entry_dec = _to_decimal(trade.entry)
                sl_dec = _to_decimal(trade.stop_loss)
                targets_list = [
                    {"price": _to_decimal(t.get("price")), "close_percent": t.get("close_percent", 0.0)}
                    for t in (trade.targets or []) if t.get("price") is not None
                ]
                user = getattr(trade, 'user', None)
                if not user:
                    log.warning(f"Skipping trigger build for UserTrade ID {trade.id}: User relationship not loaded.")
                    return None

                return {
                    "id": trade.id,
                    "item_type": "user_trade",
                    "user_id": str(user.telegram_user_id),
                    "user_db_id": trade.user_id,
                    "asset": trade.asset,
                    "side": trade.side,
                    "entry": entry_dec,
                    "stop_loss": sl_dec,
                    "targets": targets_list,
                    "status": trade.status,
                    "order_type": OrderTypeEnum.LIMIT, # User trades default to LIMIT logic
                    "market": "Futures", # User trades default to Futures
                    "processed_events": {e.event_type for e in (getattr(trade, 'events', []) or [])},
                    "profit_stop_mode": "NONE",
                    "profit_stop_price": None,
                    "profit_stop_trailing_value": None,
                    "profit_stop_active": False,
                    "original_published_at": trade.original_published_at,
                }
        except Exception as e:
            log.error(f"Failed to build trigger data for item: {e}", exc_info=True)
            return None
        return None

    # --- ✅ NEW (ADR-001): Smart Indexing Methods ---

    async def add_trigger_data(self, item_data: Dict[str, Any]):
        """
        Instantly adds a single pre-built trigger dictionary to the in-memory index.
        This is called by TradeService *after* a new trade is created.
        """
        if not item_data:
            log.warning("add_trigger_data received empty item_data.")
            return

        item_id = item_data.get("id")
        item_type = item_data.get("item_type")
        log.info(f"Smart Indexing: Adding {item_type} #{item_id} to in-memory triggers.")

        try:
            asset = item_data.get("asset")
            if not asset:
                log.error(f"Cannot add trigger {item_id}: Missing asset.")
                return
            
            trigger_key = f"{asset.upper()}:{item_data.get('market', 'Futures')}"
            
            async with self._triggers_lock:
                if trigger_key not in self.active_triggers:
                    self.active_triggers[trigger_key] = []
                
                # Avoid duplicates if full sync ran
                if not any(t['id'] == item_id and t['item_type'] == item_type for t in self.active_triggers[trigger_key]):
                    self.active_triggers[trigger_key].append(item_data)
                    
                    if item_type == "recommendation":
                        self.strategy_engine.initialize_state_for_recommendation(item_data)
                    
                    log.debug(f"Successfully added trigger {item_type} #{item_id} to key {trigger_key}.")
                else:
                    log.debug(f"Trigger {item_type} #{item_id} already in index, skipping add.")
                    
        except Exception as e:
            log.error(f"Failed to add trigger {item_type} #{item_id} to index: {e}", exc_info=True)

    async def remove_single_trigger(self, item_type: str, item_id: int):
        """
        Instantly removes a single trigger from the in-memory index by its ID and type.
        This is called by TradeService *after* a trade is closed.
        """
        log.info(f"Smart Indexing: Removing {item_type} #{item_id} from in-memory triggers.")
        found_and_removed = False
        try:
            async with self._triggers_lock:
                keys_to_check = list(self.active_triggers.keys())
                for key in keys_to_check:
                    triggers_list = self.active_triggers[key]
                    
                    # Find the item to remove
                    item_to_remove = next((t for t in triggers_list if t['id'] == item_id and t['item_type'] == item_type), None)
                    
                    if item_to_remove:
                        triggers_list.remove(item_to_remove)
                        found_and_removed = True
                        if not triggers_list:
                            # Clean up empty asset keys
                            del self.active_triggers[key]
                        break # Found it, no need to search other keys
            
            if found_and_removed:
                if item_type == "recommendation":
                    self.strategy_engine.clear_state(item_id)
                log.debug(f"Successfully removed {item_type} #{item_id} from index.")
            else:
                log.warning(f"Could not find {item_type} #{item_id} in index to remove (might be harmless if full sync ran).")
                
        except Exception as e:
            log.error(f"Failed to remove trigger {item_type} #{item_id}: {e}", exc_info=True)


    # --- Original Indexing & Processing (Unchanged) ---
    async def build_triggers_index(self):
        """Builds the in-memory index of all active triggers from the database."""
        log.info("Attempting to build in-memory trigger index (Unified)...")
        try:
            with session_scope() as session:
                trigger_data_list = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.critical("CRITICAL: DB read failure during trigger index build.", exc_info=True)
            return

        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        active_rec_ids = set()
        active_item_ids = set()

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
                if trigger_key not in new_triggers:
                    new_triggers[trigger_key] = []
                new_triggers[trigger_key].append(item)
            except Exception:
                log.exception("Failed processing trigger item: %s", item.get('id'))
        
        async with self._triggers_lock:
            self.active_triggers = new_triggers

        stale_rec_ids = set(self.strategy_engine._state.keys()) - active_rec_ids
        for rec_id in stale_rec_ids:
            self.strategy_engine.clear_state(rec_id)

        log.info("✅ Trigger index rebuilt: %d items across %d asset/market pairs.",
                 len(trigger_data_list), len(new_triggers))

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
                    strategy_actions = []
                    if trigger.get("item_type") == "recommendation":
                        strategy_actions = self.strategy_engine.evaluate(trigger, high_price, low_price)

                    core_actions = await self._evaluate_core_triggers(trigger, high_price, low_price)
                    all_actions = strategy_actions + core_actions
                    if not all_actions:
                        continue

                    close_action = next((a for a in all_actions if isinstance(a, CloseAction)), None)
                    if close_action:
                        # ✅ MODIFIED (ADR-001 Efficiency Hotfix):
                        # Pass rebuild_alerts=False. TradeService will handle the
                        # smart removal from the index.
                        await self.trade_service.close_recommendation_async(
                            rec_id=close_action.rec_id,
                            user_id=trigger['user_id'],
                            exit_price=close_action.price,
                            reason=close_action.reason,
                            rebuild_alerts=False # ✅ This is the fix
                        )
                        # We still clear the strategy engine state here
                        self.strategy_engine.clear_state(close_action.rec_id)
                        continue

                    for action in all_actions:
                        if isinstance(action, MoveSLAction):
                            # This service call doesn't trigger a rebuild, so it's fine
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
        if hasattr(self.streamer, "stop"):
            self.streamer.stop()
        if self._bg_loop:
            tasks = [self._processing_task, self._index_sync_task]
            for t in tasks:
                if t and not t.done():
                    self._bg_loop.call_soon_threadsafe(t.cancel)
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        if self._bg_thread:
            self._bg_thread.join(timeout=5.0)
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
        """Evaluates core triggers for both Recommendations and UserTrades."""
        actions: List[BaseAction] = []
        item_id = trigger["id"]
        status = trigger["status"]
        side = trigger["side"]
        item_type = trigger.get("item_type", "recommendation")
        processed_events = trigger.get("processed_events", set())

        try:
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

            elif item_type == "user_trade":
                if status in (UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION):
                    entry_price, sl_price = trigger["entry"], trigger["stop_loss"]
                    published_at = trigger.get("original_published_at")
                    
                    if published_at and datetime.now(timezone.utc) < published_at:
                        return actions

                    if "INVALIDATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        await self.trade_service.process_user_trade_invalidation_event(item_id, sl_price)
                    elif "ACTIVATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                        await self.trade_service.process_user_trade_activation_event(item_id)

                elif status == UserTradeStatusEnum.ACTIVATED:
                    sl_price = trigger["stop_loss"]
                    if "SL_HIT" not in processed_events and "FINAL_CLOSE" not in processed_events and self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        await self.trade_service.process_user_trade_sl_hit_event(item_id, sl_price)
                        return actions # Stop further processing if SL hit

                    for i, target in enumerate(trigger["targets"], 1):
                        event_name = f"TP{i}_HIT"
                        if event_name in processed_events:
                            continue
                        target_price = Decimal(str(target['price']))
                        if self._is_price_condition_met(side, low_price, high_price, target_price, "TP"):
                            await self.trade_service.process_user_trade_tp_hit_event(item_id, i, target_price)

        except Exception as e:
            log.error(f"Error evaluating core trigger for {item_type} #{item_id}: {e}", exc_info=True)

        return actions
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---