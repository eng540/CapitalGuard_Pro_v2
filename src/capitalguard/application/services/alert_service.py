# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---v28
"""
AlertService v28.1-R3 (Final corrected)
- Fully compatible with StrategyEngine v4 (pure engine returning Actions).
- Correct async usage (await evaluate / await evaluate_batch).
- Uses tick dict format: {"high","low","close","ts"}.
- Executes Actions exclusively via LifecycleService async APIs.
- Uses evaluate_batch for recommendations sharing same tick to improve throughput.
- Robust error handling and logging.
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
    Recommendation,
    UserTrade,
    RecommendationStatusEnum,
    UserTradeStatusEnum,
    OrderTypeEnum,
)
from capitalguard.application.strategy.engine import (
    StrategyEngine,
    BaseAction,
    CloseAction,
    MoveSLAction,
    AlertAction,
)
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer

# Type hints for services (avoid circular imports at runtime)
if False:
    from .lifecycle_service import LifecycleService
    from .price_service import PriceService

log = logging.getLogger(__name__)


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    if value is None:
        return default
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError):
        log.debug("AlertService: Could not convert '%s' to Decimal.", value)
        return default


class AlertService:
    """
    AlertService - orchestrates price ticks -> strategy evaluation -> lifecycle execution.
    Design principles:
      - StrategyEngine is pure: returns Actions only.
      - AlertService executes Actions via LifecycleService.
      - Minimize per-rec locks by using engine.evaluate_batch for groups sharing a tick.
      - Maintain in-memory index (smart indexing) with async locks.
    """

    def __init__(
        self,
        lifecycle_service: "LifecycleService",
        price_service: "PriceService",
        repo: RecommendationRepository,
        strategy_engine: StrategyEngine,
        streamer: Optional[PriceStreamer] = None,
    ):
        self.lifecycle_service = lifecycle_service
        self.price_service = price_service
        self.repo = repo
        self.strategy_engine = strategy_engine

        # Note: Queue & Locks are created here but exclusively used within BG loop.
        # If you move queue creation outside the BG loop, ensure thread-safe interactions.
        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)

        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()

        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None

    # ---------------------------------------------------------------------
    # Background runner
    # ---------------------------------------------------------------------
    def start(self):
        """Start background thread hosting an asyncio event loop that runs processing tasks."""
        if self._bg_thread and self._bg_thread.is_alive():
            log.warning("AlertService already running.")
            return

        def _bg_runner():
            try:
                loop = asyncio.new_event_loop()
                self._bg_loop = loop
                asyncio.set_event_loop(loop)

                # Create background tasks inside the event loop
                self._processing_task = loop.create_task(self._process_queue())
                self._index_sync_task = loop.create_task(self._run_index_sync())

                # Start streamer if available (pass loop if streamer supports it)
                if hasattr(self.streamer, "start"):
                    try:
                        self.streamer.start(loop=loop)
                    except TypeError:
                        # older streamer implementations may not accept loop parameter
                        self.streamer.start()

                loop.run_forever()
            except Exception:
                log.exception("AlertService background runner crashed.")
            finally:
                try:
                    if self._bg_loop and self._bg_loop.is_running():
                        self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
                except Exception:
                    pass

        self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
        self._bg_thread.start()
        log.info("AlertService background thread started.")

    # ---------------------------------------------------------------------
    # Index builders / helpers
    # ---------------------------------------------------------------------
    def build_trigger_data_from_orm(self, item_orm: Union[Recommendation, UserTrade]) -> Optional[Dict[str, Any]]:
        """Convert ORM object to canonical trigger dict used by engine and alert service."""
        try:
            if isinstance(item_orm, Recommendation):
                rec = item_orm
                entry_dec = _to_decimal(rec.entry)
                sl_dec = _to_decimal(rec.stop_loss)
                targets_list = [
                    {"price": _to_decimal(t.get("price")), "close_percent": t.get("close_percent", 0.0)}
                    for t in (rec.targets or []) if t.get("price") is not None
                ]
                user = getattr(rec, "analyst", None)
                if not user:
                    log.warning("Skipping Rec %s: analyst missing.", rec.id)
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
                    "processed_events": {e.event_type for e in (getattr(rec, "events", []) or [])},
                    "profit_stop_mode": getattr(rec, "profit_stop_mode", "NONE"),
                    "profit_stop_price": _to_decimal(getattr(rec, "profit_stop_price", None)) if getattr(rec, "profit_stop_price", None) is not None else None,
                    "profit_stop_trailing_value": _to_decimal(getattr(rec, "profit_stop_trailing_value", None)) if getattr(rec, "profit_stop_trailing_value", None) is not None else None,
                    "profit_stop_active": getattr(rec, "profit_stop_active", False),
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
                user = getattr(trade, "user", None)
                if not user:
                    log.warning("Skipping UserTrade %s: user missing.", trade.id)
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
                    "order_type": OrderTypeEnum.LIMIT,
                    "market": "Futures",
                    "processed_events": {e.event_type for e in (getattr(trade, "events", []) or [])},
                    "profit_stop_mode": "NONE",
                    "profit_stop_price": None,
                    "profit_stop_trailing_value": None,
                    "profit_stop_active": False,
                    "original_published_at": trade.original_published_at,
                }
        except Exception:
            log.exception("Failed to build trigger data from ORM.")
            return None
        return None

    async def add_trigger_data(self, item_data: Dict[str, Any]):
        """Add single trigger to in-memory index (smart indexing)."""
        if not item_data:
            return
        item_id = item_data.get("id")
        item_type = item_data.get("item_type")
        asset = item_data.get("asset")
        if not asset:
            log.error("add_trigger_data: missing asset for item %s", item_id)
            return
        key = f"{asset.upper()}:{item_data.get('market', 'Futures')}"
        try:
            async with self._triggers_lock:
                lst = self.active_triggers.setdefault(key, [])
                if not any(t['id'] == item_id and t['item_type'] == item_type for t in lst):
                    lst.append(item_data)
                    if item_type == "recommendation":
                        # initialize engine state for new recommendation
                        self.strategy_engine.initialize_state_for_recommendation(item_data)
        except Exception:
            log.exception("add_trigger_data failed for %s", item_id)

    async def remove_single_trigger(self, item_type: str, item_id: int):
        """Remove single trigger by id and type."""
        try:
            async with self._triggers_lock:
                keys = list(self.active_triggers.keys())
                for key in keys:
                    lst = self.active_triggers.get(key, [])
                    obj = next((t for t in lst if t['id'] == item_id and t['item_type'] == item_type), None)
                    if obj:
                        lst.remove(obj)
                        if not lst:
                            del self.active_triggers[key]
                        break
            if item_type == "recommendation":
                self.strategy_engine.clear_state(item_id)
        except Exception:
            log.exception("remove_single_trigger failed for %s:%s", item_type, item_id)
            return

    async def build_triggers_index(self):
        """Build or rebuild full in-memory index from DB."""
        try:
            with session_scope() as session:
                items = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.exception("Failed reading triggers from DB.")
            return

        new_index: Dict[str, List[Dict[str, Any]]] = {}
        for d in items:
            if not d:
                continue
            try:
                asset = d.get("asset")
                if not asset:
                    continue
                key = f"{asset.upper()}:{d.get('market', 'Futures')}"
                new_index.setdefault(key, []).append(d)
            except Exception:
                log.exception("Failed to process trigger item: %s", d.get("id"))

        try:
            async with self._triggers_lock:
                self.active_triggers = new_index
                self.strategy_engine.clear_all_states()
                for key, triggers in new_index.items():
                    for t in triggers:
                        if t.get("item_type") == "recommendation":
                            self.strategy_engine.initialize_state_for_recommendation(t)
            log.info("Trigger index rebuilt: %d keys", len(new_index))
        except Exception:
            log.exception("Failed to apply new triggers index.")

    async def _run_index_sync(self, interval_seconds: int = 60):
        """Periodic full index sync to pick up DB changes."""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.build_triggers_index()
            except Exception:
                log.exception("Index sync iteration failed.")

    # ---------------------------------------------------------------------
    # Core evaluation helpers
    # ---------------------------------------------------------------------
    def _is_price_condition_met(self, side: str, low_price: Decimal, high_price: Decimal, target_price: Decimal, condition_type: str) -> bool:
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()
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
        Evaluate built-in core triggers (activation/invalidation/SL/TP) which are executed
        as LifecycleService calls or produce CloseAction.
        """
        actions: List[BaseAction] = []
        item_id = trigger.get("id")
        status = trigger.get("status")
        side = trigger.get("side")
        item_type = trigger.get("item_type", "recommendation")
        processed_events = trigger.get("processed_events", set())

        try:
            if item_type == "recommendation":
                if status == RecommendationStatusEnum.PENDING:
                    entry_price = trigger.get("entry")
                    sl_price = trigger.get("stop_loss")

                    if "INVALIDATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        await self.lifecycle_service.process_invalidation_event(item_id)
                    elif "ACTIVATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                        await self.lifecycle_service.process_activation_event(item_id)

                elif status == RecommendationStatusEnum.ACTIVE:
                    sl_price = trigger.get("stop_loss")
                    if "SL_HIT" not in processed_events and "FINAL_CLOSE" not in processed_events:
                        if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                            actions.append(CloseAction(rec_id=item_id, price=sl_price, reason="SL_HIT"))
                            return actions

                    for i, target in enumerate(trigger.get("targets", []) or [], 1):
                        event_name = f"TP{i}_HIT"
                        if event_name in processed_events:
                            continue
                        tp_price = Decimal(str(target.get("price")))
                        if self._is_price_condition_met(side, low_price, high_price, tp_price, "TP"):
                            await self.lifecycle_service.process_tp_hit_event(item_id, i, tp_price)

            elif item_type == "user_trade":
                if status in (UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION):
                    entry_price = trigger.get("entry")
                    sl_price = trigger.get("stop_loss")
                    published_at = trigger.get("original_published_at")
                    if published_at and datetime.now(timezone.utc) < published_at:
                        return actions

                    if "INVALIDATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                        await self.lifecycle_service.process_user_trade_invalidation_event(item_id, sl_price)
                    elif "ACTIVATED" not in processed_events and self._is_price_condition_met(side, low_price, high_price, entry_price, "ENTRY"):
                        await self.lifecycle_service.process_user_trade_activation_event(item_id)

                elif status == UserTradeStatusEnum.ACTIVATED:
                    sl_price = trigger.get("stop_loss")
                    if "SL_HIT" not in processed_events and "FINAL_CLOSE" not in processed_events:
                        if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                            await self.lifecycle_service.process_user_trade_sl_hit_event(item_id, sl_price)
                            return actions

                    for i, target in enumerate(trigger.get("targets", []) or [], 1):
                        event_name = f"TP{i}_HIT"
                        if event_name in processed_events:
                            continue
                        tp_price = Decimal(str(target.get("price")))
                        if self._is_price_condition_met(side, low_price, high_price, tp_price, "TP"):
                            await self.lifecycle_service.process_user_trade_tp_hit_event(item_id, i, tp_price)

        except Exception:
            log.exception("Error evaluating core trigger for %s id=%s", item_type, item_id)

        return actions

    # ---------------------------------------------------------------------
    # Main queue processor (uses evaluate_batch where possible)
    # ---------------------------------------------------------------------
    async def _process_queue(self):
        log.info("AlertService queue processor started.")
        while True:
            try:
                # PriceStreamer should push tuples like: (symbol, market, low, high, close_optional?)
                payload = await self.price_queue.get()
                # normalize incoming payload shape: support legacy (symbol, market, low, high) or (symbol, market, low, high, close)
                if isinstance(payload, (list, tuple)):
                    if len(payload) == 4:
                        symbol, market, low_raw, high_raw = payload
                        close_raw = high_raw
                    elif len(payload) >= 5:
                        symbol, market, low_raw, high_raw, close_raw = payload[:5]
                    else:
                        log.debug("Unexpected price_queue payload shape: %s", payload)
                        self.price_queue.task_done()
                        continue
                elif isinstance(payload, dict):
                    symbol = payload.get("symbol")
                    market = payload.get("market", "Futures")
                    low_raw = payload.get("low")
                    high_raw = payload.get("high")
                    close_raw = payload.get("close", high_raw)
                else:
                    log.debug("Unknown payload type in price_queue: %r", payload)
                    self.price_queue.task_done()
                    continue

                try:
                    low_price = Decimal(str(low_raw))
                    high_price = Decimal(str(high_raw))
                    close_price = Decimal(str(close_raw))
                except Exception:
                    log.exception("Invalid price values in payload: %s", payload)
                    self.price_queue.task_done()
                    continue

                tick_ts = int(time.time())
                tick = {"high": high_price, "low": low_price, "close": close_price, "ts": tick_ts}
                key = f"{(symbol or '').upper()}:{market}"

                # snapshot triggers under lock
                async with self._triggers_lock:
                    triggers_for_key = list(self.active_triggers.get(key, []))

                if not triggers_for_key:
                    self.price_queue.task_done()
                    continue

                # Group recommendation triggers for batch evaluation
                rec_triggers = [t for t in triggers_for_key if t.get("item_type") == "recommendation"]
                other_triggers = [t for t in triggers_for_key if t.get("item_type") != "recommendation"]

                strategy_actions_all: List[BaseAction] = []
                # Evaluate recommendations in batch for the same tick to reduce overhead
                if rec_triggers:
                    try:
                        batch_actions = await self.strategy_engine.evaluate_batch(rec_triggers, tick)
                        if batch_actions:
                            strategy_actions_all.extend(batch_actions)
                    except Exception:
                        log.exception("StrategyEngine.evaluate_batch failed for key=%s", key)

                # For non-recommendation triggers run per-trigger evaluate if engine supports them (rare)
                for trig in other_triggers:
                    try:
                        # Engine.evaluate expected for single rec-like objects
                        actions = await self.strategy_engine.evaluate(trig, tick)
                        if actions:
                            strategy_actions_all.extend(actions)
                    except Exception:
                        log.exception("StrategyEngine.evaluate failed for non-recommendation trigger id=%s", trig.get("id"))

                # Evaluate core triggers (activation/invalidation/TP/SL) and execute lifecycle actions
                # We'll collect all core actions per trigger and then merge with strategy actions.
                core_actions_all: List[BaseAction] = []
                for trig in triggers_for_key:
                    try:
                        actions = await self._evaluate_core_triggers(trig, high_price, low_price)
                        if actions:
                            core_actions_all.extend(actions)
                    except Exception:
                        log.exception("Core evaluate failed for trigger id=%s", trig.get("id"))

                # Merge actions
                all_actions: List[BaseAction] = []
                all_actions.extend(strategy_actions_all)
                all_actions.extend(core_actions_all)

                if not all_actions:
                    self.price_queue.task_done()
                    continue

                # Prefer immediate CloseAction handling first to avoid conflicting SL moves
                close_action = next((a for a in all_actions if isinstance(a, CloseAction)), None)
                if close_action:
                    try:
                        await self.lifecycle_service.close_recommendation_async(
                            rec_id=close_action.rec_id,
                            user_id=self._find_user_id_for_rec(close_action.rec_id, triggers_for_key),
                            exit_price=close_action.price,
                            reason=getattr(close_action, "reason", "CLOSE"),
                            rebuild_alerts=False,
                        )
                    except Exception:
                        log.exception("Failed to execute close_recommendation_async for rec=%s", close_action.rec_id)
                    # clear engine state to avoid duplicated actions
                    try:
                        self.strategy_engine.clear_state(close_action.rec_id)
                    except Exception:
                        log.exception("Failed to clear engine state for rec=%s", close_action.rec_id)
                    # we've handled the close; continue to next tick
                    self.price_queue.task_done()
                    continue

                # Execute other actions (MoveSLAction, AlertAction, etc.)
                for act in all_actions:
                    try:
                        if isinstance(act, MoveSLAction):
                            await self.lifecycle_service.update_sl_for_user_async(
                                rec_id=act.rec_id,
                                user_id=self._find_user_id_for_rec(act.rec_id, triggers_for_key),
                                new_sl=act.new_sl,
                            )
                        elif isinstance(act, AlertAction):
                            # optional: dispatch notification via lifecycle_service or notifier
                            if hasattr(self.lifecycle_service, "send_alert_async"):
                                try:
                                    await self.lifecycle_service.send_alert_async(
                                        rec_id=act.rec_id,
                                        level=getattr(act, "level", "info"),
                                        message=getattr(act, "message", ""),
                                        metadata=getattr(act, "metadata", None),
                                    )
                                except Exception:
                                    log.exception("send_alert_async failed for act=%s", act)
                        else:
                            # unknown action type - log for inspection
                            log.debug("Unhandled action type %s for rec=%s", type(act), getattr(act, "rec_id", None))
                    except Exception:
                        log.exception("Failed executing action %s for rec=%s", type(act), getattr(act, "rec_id", None))

                self.price_queue.task_done()

            except asyncio.CancelledError:
                log.info("Queue processor cancelled.")
                break
            except Exception:
                log.exception("Unexpected error in queue processor.")
                # do not re-raise; continue loop

    # ---------------------------------------------------------------------
    # Utility
    # ---------------------------------------------------------------------
    def _find_user_id_for_rec(self, rec_id: int, trig_list: List[Dict[str, Any]]) -> Optional[str]:
        """Helper to find user_id for a given rec_id in current triggers snapshot."""
        for t in trig_list:
            if t.get("id") == rec_id:
                return t.get("user_id")
        return None

    def stop(self):
        """Stop background tasks and streamer (best-effort)."""
        try:
            if hasattr(self.streamer, "stop"):
                try:
                    self.streamer.stop()
                except Exception:
                    log.exception("Error stopping streamer.")

            if self._bg_loop:
                if self._processing_task:
                    try:
                        self._bg_loop.call_soon_threadsafe(self._processing_task.cancel)
                    except Exception:
                        pass
                if self._index_sync_task:
                    try:
                        self._bg_loop.call_soon_threadsafe(self._index_sync_task.cancel)
                    except Exception:
                        pass
                try:
                    self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
                except Exception:
                    pass

            if self._bg_thread:
                self._bg_thread.join(timeout=5.0)
        except Exception:
            log.exception("Error stopping AlertService.")
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---