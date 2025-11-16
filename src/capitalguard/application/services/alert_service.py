# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---v 4
import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional, Union
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import time

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
)
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer

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
        return default


class AlertService:
    """
    AlertService v28.0-R2-Rebuild
    - إعادة بناء كاملة متوافقة مع StrategyEngine v4.0
    - يعتمد على evaluate async
    - يعتمد tick القياسي: {"high":..., "low":..., "close":..., "ts":...}
    - تنفيذ Actions يتم فقط عن طريق LifecycleService
    - Smart Indexing محسّن
    - تزامن كامل عبر asyncio.Lock
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

        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)

        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()

        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None

    # -----------------------------------------------------------------------------------------
    # Background Thread Start
    # -----------------------------------------------------------------------------------------
    def start(self):
        if self._bg_thread and self._bg_thread.is_alive():
            return

        def _runner():
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

        self._bg_thread = threading.Thread(
            target=_runner, name="alertservice-bg", daemon=True
        )
        self._bg_thread.start()

    # -----------------------------------------------------------------------------------------
    # Trigger Builders
    # -----------------------------------------------------------------------------------------
    def build_trigger_data_from_orm(
        self, item_orm: Union[Recommendation, UserTrade]
    ) -> Optional[Dict[str, Any]]:
        try:
            if isinstance(item_orm, Recommendation):
                rec = item_orm
                entry_dec = _to_decimal(rec.entry)
                sl_dec = _to_decimal(rec.stop_loss)

                targets_list = [
                    {
                        "price": _to_decimal(t.get("price")),
                        "close_percent": t.get("close_percent", 0.0),
                    }
                    for t in (rec.targets or [])
                    if t.get("price") is not None
                ]

                user = getattr(rec, "analyst", None)
                if not user:
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
                    "processed_events": {
                        e.event_type for e in (getattr(rec, "events", []) or [])
                    },
                    "profit_stop_mode": getattr(rec, "profit_stop_mode", "NONE"),
                    "profit_stop_price": _to_decimal(
                        getattr(rec, "profit_stop_price", None)
                    )
                    if getattr(rec, "profit_stop_price", None) is not None
                    else None,
                    "profit_stop_trailing_value": _to_decimal(
                        getattr(rec, "profit_stop_trailing_value", None)
                    )
                    if getattr(rec, "profit_stop_trailing_value", None) is not None
                    else None,
                    "profit_stop_active": getattr(rec, "profit_stop_active", False),
                    "original_published_at": None,
                }

            elif isinstance(item_orm, UserTrade):
                trade = item_orm
                entry_dec = _to_decimal(trade.entry)
                sl_dec = _to_decimal(trade.stop_loss)

                targets_list = [
                    {
                        "price": _to_decimal(t.get("price")),
                        "close_percent": t.get("close_percent", 0.0),
                    }
                    for t in (trade.targets or [])
                    if t.get("price") is not None
                ]

                user = getattr(trade, "user", None)
                if not user:
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
                    "processed_events": {
                        e.event_type for e in (getattr(trade, "events", []) or [])
                    },
                    "profit_stop_mode": "NONE",
                    "profit_stop_price": None,
                    "profit_stop_trailing_value": None,
                    "profit_stop_active": False,
                    "original_published_at": trade.original_published_at,
                }

        except Exception:
            log.exception("Failed to build trigger from ORM")
            return None

        return None

    # -----------------------------------------------------------------------------------------
    # Smart Index: Add
    # -----------------------------------------------------------------------------------------
    async def add_trigger_data(self, item_data: Dict[str, Any]):
        if not item_data:
            return

        item_id = item_data.get("id")
        item_type = item_data.get("item_type")
        asset = item_data.get("asset")
        if not asset:
            return

        key = f"{asset.upper()}:{item_data.get('market', 'Futures')}"

        try:
            async with self._triggers_lock:
                if key not in self.active_triggers:
                    self.active_triggers[key] = []

                exists = any(
                    t["id"] == item_id and t["item_type"] == item_type
                    for t in self.active_triggers[key]
                )
                if not exists:
                    self.active_triggers[key].append(item_data)

                    if item_type == "recommendation":
                        self.strategy_engine.initialize_state_for_recommendation(
                            item_data
                        )
        except Exception:
            log.exception("Failed to add trigger")

    # -----------------------------------------------------------------------------------------
    # Smart Index: Remove
    # -----------------------------------------------------------------------------------------
    async def remove_single_trigger(self, item_type: str, item_id: int):
        try:
            async with self._triggers_lock:
                keys = list(self.active_triggers.keys())
                for key in keys:
                    lst = self.active_triggers[key]
                    obj = next(
                        (
                            t
                            for t in lst
                            if t["id"] == item_id and t["item_type"] == item_type
                        ),
                        None,
                    )
                    if obj:
                        lst.remove(obj)
                        if not lst:
                            del self.active_triggers[key]
                        if item_type == "recommendation":
                            self.strategy_engine.clear_state(item_id)
                        break
        except Exception:
            log.exception("Failed to remove trigger")

    # -----------------------------------------------------------------------------------------
    # Smart Index: Full Rebuild
    # -----------------------------------------------------------------------------------------
    async def build_triggers_index(self):
        try:
            with session_scope() as session:
                items = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.exception("Index build failed")
            return

        new_index: Dict[str, List[Dict[str, Any]]] = {}

        for d in items:
            try:
                asset = d.get("asset")
                if not asset:
                    continue
                key = f"{asset.upper()}:{d.get('market', 'Futures')}"
                new_index.setdefault(key, []).append(d)
            except Exception:
                log.exception("Index item failed")

        async with self._triggers_lock:
            self.active_triggers = new_index
            self.strategy_engine.clear_all_states()
            for key, triggers in new_index.items():
                for t in triggers:
                    if t.get("item_type") == "recommendation":
                        self.strategy_engine.initialize_state_for_recommendation(t)

    async def _run_index_sync(self, interval_seconds: int = 60):
        while True:
            await asyncio.sleep(interval_seconds)
            await self.build_triggers_index()

    # -----------------------------------------------------------------------------------------
    # Helper: Price Condition
    # -----------------------------------------------------------------------------------------
    def _is_price_condition_met(
        self,
        side: str,
        low_price: Decimal,
        high_price: Decimal,
        target_price: Decimal,
        condition_type: str,
    ) -> bool:
        s = side.upper()
        c = condition_type.upper()

        if s == "LONG":
            if c.startswith("TP"):
                return high_price >= target_price
            if c == "SL":
                return low_price <= target_price
            if c == "ENTRY":
                return low_price <= target_price

        elif s == "SHORT":
            if c.startswith("TP"):
                return low_price <= target_price
            if c == "SL":
                return high_price >= target_price
            if c == "ENTRY":
                return high_price >= target_price

        return False

    # -----------------------------------------------------------------------------------------
    # Core Trigger Evaluator
    # -----------------------------------------------------------------------------------------
    async def _evaluate_core_triggers(
        self, trigger: Dict[str, Any], high_price: Decimal, low_price: Decimal
    ) -> List[BaseAction]:
        actions: List[BaseAction] = []

        item_id = trigger["id"]
        status = trigger["status"]
        side = trigger["side"]
        item_type = trigger.get("item_type", "recommendation")
        processed = trigger.get("processed_events", set())

        try:
            # ---------------------------------------------------------------------------------
            # Recommendation
            # ---------------------------------------------------------------------------------
            if item_type == "recommendation":
                if status == RecommendationStatusEnum.PENDING:
                    entry = trigger["entry"]
                    sl = trigger["stop_loss"]

                    if "INVALIDATED" not in processed and self._is_price_condition_met(
                        side, low_price, high_price, sl, "SL"
                    ):
                        await self.lifecycle_service.process_invalidation_event(item_id)

                    elif "ACTIVATED" not in processed and self._is_price_condition_met(
                        side, low_price, high_price, entry, "ENTRY"
                    ):
                        await self.lifecycle_service.process_activation_event(item_id)

                elif status == RecommendationStatusEnum.ACTIVE:
                    sl = trigger["stop_loss"]

                    if "SL_HIT" not in processed and "FINAL_CLOSE" not in processed:
                        if self._is_price_condition_met(
                            side, low_price, high_price, sl, "SL"
                        ):
                            actions.append(
                                CloseAction(
                                    rec_id=item_id, price=sl, reason="SL_HIT"
                                )
                            )
                            return actions

                    for i, tgt in enumerate(trigger["targets"], 1):
                        tp_price = Decimal(str(tgt["price"]))
                        ev = f"TP{i}_HIT"
                        if ev in processed:
                            continue
                        if self._is_price_condition_met(
                            side, low_price, high_price, tp_price, "TP"
                        ):
                            await self.lifecycle_service.process_tp_hit_event(
                                item_id, i, tp_price
                            )

            # ---------------------------------------------------------------------------------
            # UserTrade
            # ---------------------------------------------------------------------------------
            elif item_type == "user_trade":
                if status in (
                    UserTradeStatusEnum.WATCHLIST,
                    UserTradeStatusEnum.PENDING_ACTIVATION,
                ):
                    entry = trigger["entry"]
                    sl = trigger["stop_loss"]
                    pub_at = trigger.get("original_published_at")

                    if pub_at and datetime.now(timezone.utc) < pub_at:
                        return actions

                    if "INVALIDATED" not in processed and self._is_price_condition_met(
                        side, low_price, high_price, sl, "SL"
                    ):
                        await self.lifecycle_service.process_user_trade_invalidation_event(
                            item_id, sl
                        )

                    elif "ACTIVATED" not in processed and self._is_price_condition_met(
                        side, low_price, high_price, entry, "ENTRY"
                    ):
                        await self.lifecycle_service.process_user_trade_activation_event(
                            item_id
                        )

                elif status == UserTradeStatusEnum.ACTIVATED:
                    sl = trigger["stop_loss"]

                    if "SL_HIT" not in processed and "FINAL_CLOSE" not in processed:
                        if self._is_price_condition_met(
                            side, low_price, high_price, sl, "SL"
                        ):
                            await self.lifecycle_service.process_user_trade_sl_hit_event(
                                item_id, sl
                            )
                            return actions

                    for i, tgt in enumerate(trigger["targets"], 1):
                        ev = f"TP{i}_HIT"
                        if ev in processed:
                            continue
                        tp_price = Decimal(str(tgt["price"]))
                        if self._is_price_condition_met(
                            side, low_price, high_price, tp_price, "TP"
                        ):
                            await self.lifecycle_service.process_user_trade_tp_hit_event(
                                item_id, i, tp_price
                            )

        except Exception:
            log.exception("Core trigger evaluation error")

        return actions

    # -----------------------------------------------------------------------------------------
    # Queue Processor — R2 Rebuild
    # -----------------------------------------------------------------------------------------
    async def _process_queue(self):
        while True:
            try:
                symbol, market, low_str, high_str = await self.price_queue.get()

                low_price = Decimal(str(low_str))
                high_price = Decimal(str(high_str))

                tick = {
                    "high": high_price,
                    "low": low_price,
                    "close": high_price,
                    "ts": int(time.time()),
                }

                key = f"{symbol.upper()}:{market}"

                async with self._triggers_lock:
                    triggers = list(self.active_triggers.get(key, []))

                if not triggers:
                    self.price_queue.task_done()
                    continue

                for trig in triggers:
                    strategy_actions: List[BaseAction] = []

                    if trig.get("item_type") == "recommendation":
                        try:
                            strategy_actions = await self.strategy_engine.evaluate(
                                trig, tick
                            )
                        except Exception:
                            log.exception(
                                "StrategyEngine.evaluate failed for rec_id=%s",
                                trig.get("id"),
                            )
                            strategy_actions = []

                    core_actions = await self._evaluate_core_triggers(
                        trig, high_price, low_price
                    )

                    all_actions = strategy_actions + core_actions
                    if not all_actions:
                        continue

                    close_act = next(
                        (a for a in all_actions if isinstance(a, CloseAction)), None
                    )
                    if close_act:
                        try:
                            await self.lifecycle_service.close_recommendation_async(
                                rec_id=close_act.rec_id,
                                user_id=trig["user_id"],
                                exit_price=close_act.price,
                                reason=close_act.reason,
                                rebuild_alerts=False,
                            )
                        except Exception:
                            log.exception(
                                "Lifecycle close_recommendation_async failed"
                            )
                        self.strategy_engine.clear_state(close_act.rec_id)
                        continue

                    for act in all_actions:
                        if isinstance(act, MoveSLAction):
                            try:
                                await self.lifecycle_service.update_sl_for_user_async(
                                    rec_id=act.rec_id,
                                    user_id=trig["user_id"],
                                    new_sl=act.new_sl,
                                )
                            except Exception:
                                log.exception(
                                    "Lifecycle update_sl_for_user_async failed"
                                )

                self.price_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Error in _process_queue loop")

    # -----------------------------------------------------------------------------------------
    # Stop
    # -----------------------------------------------------------------------------------------
    def stop(self):
        try:
            if hasattr(self.streamer, "stop"):
                self.streamer.stop()

            if self._bg_loop:
                if self._processing_task:
                    self._bg_loop.call_soon_threadsafe(
                        self._processing_task.cancel
                    )
                if self._index_sync_task:
                    self._bg_loop.call_soon_threadsafe(
                        self._index_sync_task.cancel
                    )
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)

            if self._bg_thread:
                self._bg_thread.join(timeout=5.0)

        except Exception:
            log.exception("Error while stopping AlertService")
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/alert_service.py ---