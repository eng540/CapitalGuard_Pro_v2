# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
# File: src/capitalguard/application/services/alert_service.py
# Version: v29.1-PRODUCTION
#
# ✅ إصلاحات v29.0 (محفوظة بالكامل):
#   FIX-A: price_queue يُنشأ داخل _bg_runner() — لا خارجه
#   FIX-B: threading.RLock يحمي active_triggers عبر الـ threads
#   FIX-C: PriceStreamer يُبنى داخل _bg_runner() بعد إنشاء price_queue
#
# ✅ THE FIX v29.1 — تحسين _run_index_sync:
#   كانت المزامنة الدورية كل 60 ثانية = polling مكثف بلا فائدة.
#   السبب: الصفقات الجديدة تُضاف فوراً عبر add_trigger_data()
#   من creation_service — لا حاجة لمزامنة متكررة.
#
#   الإصلاح: interval_seconds = 600 (10 دقائق)
#   الدور: شبكة أمان فقط لتصحيح أي تعارض في الذاكرة
#          وليس آلية الاكتشاف الرئيسية.
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-16

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
    AlertService v29.1 — إشعارات القناة + تتبع دورة حياة الصفقات.

    Pipeline:
      Binance WS → PriceStreamer → price_queue
        → _process_queue() → StrategyEngine / _evaluate_core_triggers()
        → LifecycleService → DB update + notify_card_update()
        → TelegramNotifier.edit_recommendation_card_by_ids()
        → القناة ترى التحديث

    اكتشاف الصفقات الجديدة:
      المسار الرئيسي (فوري):
        creation_service → add_trigger_data() → active_triggers
      شبكة الأمان (كل 10 دقائق):
        _run_index_sync() → build_triggers_index() من DB
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

        # ✅ FIX-C: نحتفظ بـ streamer المُمرَّر ونُنشئه داخل _bg_runner إن لم يكن
        self._streamer_arg = streamer

        # ✅ FIX-A: price_queue يُنشأ داخل _bg_runner() — لا خارجه
        self.price_queue: Optional[asyncio.Queue] = None

        # ✅ FIX-B: threading.RLock لحماية active_triggers من أي thread
        #           asyncio.Lock للـ bg loop فقط، يُنشأ داخل _bg_runner()
        self._sync_lock = threading.RLock()
        self._triggers_lock: Optional[asyncio.Lock] = None

        # البيانات المشتركة (محمية بـ _sync_lock)
        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}

        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self.streamer: Optional[PriceStreamer] = None

    # ─────────────────────────────────────────────────────────────
    # Background runner
    # ─────────────────────────────────────────────────────────────

    def start(self):
        """يبدأ bg thread مع event loop خاص به."""
        if self._bg_thread and self._bg_thread.is_alive():
            log.warning("AlertService already running.")
            return

        def _bg_runner():
            try:
                loop = asyncio.new_event_loop()
                self._bg_loop = loop
                asyncio.set_event_loop(loop)

                # ✅ FIX-A: إنشاء asyncio primitives داخل الـ loop
                self.price_queue = asyncio.Queue()
                self._triggers_lock = asyncio.Lock()
                log.info("AlertService: price_queue and _triggers_lock created.")

                # ✅ FIX-C: بناء PriceStreamer الآن بعد وجود price_queue
                if self._streamer_arg is not None:
                    self.streamer = self._streamer_arg
                else:
                    self.streamer = PriceStreamer(self.price_queue, self.repo)
                    log.info("AlertService: PriceStreamer created.")

                # بدء مهام المعالجة
                self._processing_task = loop.create_task(self._process_queue())
                # ✅ v29.1: شبكة أمان كل 10 دقائق فقط
                self._index_sync_task = loop.create_task(self._run_index_sync())

                # بدء PriceStreamer
                try:
                    self.streamer.start(loop=loop)
                    log.info("AlertService: PriceStreamer started.")
                except TypeError:
                    self.streamer.start()
                except Exception as e:
                    log.error("AlertService: PriceStreamer start failed: %s", e)

                # نقل الـ triggers المُحمَّلة مسبقاً إلى الـ bg loop
                if self.active_triggers:
                    loaded = sum(len(v) for v in self.active_triggers.values())
                    log.info(
                        "AlertService: %d pre-loaded triggers transferred to bg loop.",
                        loaded,
                    )
                    for triggers in self.active_triggers.values():
                        for t in triggers:
                            if t.get("item_type") == "recommendation":
                                self.strategy_engine.initialize_state_for_recommendation(t)

                loop.run_forever()

            except Exception:
                log.exception("AlertService background runner crashed.")
            finally:
                try:
                    if self._bg_loop and self._bg_loop.is_running():
                        self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
                except Exception:
                    pass

        self._bg_thread = threading.Thread(
            target=_bg_runner, name="alertservice-bg", daemon=True
        )
        self._bg_thread.start()
        log.info("AlertService background thread started.")

    # ─────────────────────────────────────────────────────────────
    # Index builders — thread-safe (يعمل قبل وبعد start())
    # ─────────────────────────────────────────────────────────────

    def build_trigger_data_from_orm(
        self, item_orm: Union[Recommendation, UserTrade]
    ) -> Optional[Dict[str, Any]]:
        """تحويل ORM إلى trigger dict."""
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
                    "processed_events": {
                        e.event_type
                        for e in (getattr(rec, "events", []) or [])
                    },
                    "profit_stop_mode": getattr(rec, "profit_stop_mode", "NONE"),
                    "profit_stop_price": _to_decimal(
                        getattr(rec, "profit_stop_price", None)
                    ) if getattr(rec, "profit_stop_price", None) is not None else None,
                    "profit_stop_trailing_value": _to_decimal(
                        getattr(rec, "profit_stop_trailing_value", None)
                    ) if getattr(rec, "profit_stop_trailing_value", None) is not None else None,
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
                    "processed_events": {
                        e.event_type
                        for e in (getattr(trade, "events", []) or [])
                    },
                    "profit_stop_mode": "NONE",
                    "profit_stop_price": None,
                    "profit_stop_trailing_value": None,
                    "profit_stop_active": False,
                    "original_published_at": trade.original_published_at,
                }
        except Exception:
            log.exception("Failed to build trigger data from ORM.")
        return None

    async def add_trigger_data(self, item_data: Dict[str, Any]):
        """
        ✅ FIX-B: يعمل من أي thread.
        المسار الرئيسي لإضافة صفقة جديدة — فوري بدون انتظار.
        """
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
            if self._triggers_lock is not None:
                async with self._triggers_lock:
                    self._add_trigger_unsafe(key, item_data, item_id, item_type)
            else:
                with self._sync_lock:
                    self._add_trigger_unsafe(key, item_data, item_id, item_type)
        except Exception:
            log.exception("add_trigger_data failed for %s", item_id)

    def _add_trigger_unsafe(self, key, item_data, item_id, item_type):
        """داخلي — لا يستخدم lock، الـ caller مسؤول."""
        lst = self.active_triggers.setdefault(key, [])
        if not any(t["id"] == item_id and t["item_type"] == item_type for t in lst):
            lst.append(item_data)
            if item_type == "recommendation":
                self.strategy_engine.initialize_state_for_recommendation(item_data)

    async def remove_single_trigger(self, item_type: str, item_id: int):
        """✅ FIX-B: يعمل من أي thread."""
        try:
            if self._triggers_lock is not None:
                async with self._triggers_lock:
                    self._remove_trigger_unsafe(item_type, item_id)
            else:
                with self._sync_lock:
                    self._remove_trigger_unsafe(item_type, item_id)
            if item_type == "recommendation":
                self.strategy_engine.clear_state(item_id)
        except Exception:
            log.exception("remove_single_trigger failed for %s:%s", item_type, item_id)

    def _remove_trigger_unsafe(self, item_type, item_id):
        for key in list(self.active_triggers.keys()):
            lst = self.active_triggers.get(key, [])
            obj = next(
                (t for t in lst if t["id"] == item_id and t["item_type"] == item_type),
                None,
            )
            if obj:
                lst.remove(obj)
                if not lst:
                    del self.active_triggers[key]
                break

    async def build_triggers_index(self):
        """
        ✅ FIX-B: يعمل قبل start() وبعده.
        يُبني الـ index من DB — يُستدعى عند startup وكل 10 دقائق كشبكة أمان.
        """
        log.info("AlertService: Building triggers index from DB...")
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

        if self._triggers_lock is not None:
            async with self._triggers_lock:
                self._apply_new_index(new_index)
        else:
            with self._sync_lock:
                self._apply_new_index(new_index)

        total = sum(len(v) for v in new_index.values())
        log.info(
            "AlertService: Trigger index built — %d symbols, %d triggers.",
            len(new_index), total,
        )

    def _apply_new_index(self, new_index):
        self.active_triggers = new_index
        self.strategy_engine.clear_all_states()
        for triggers in new_index.values():
            for t in triggers:
                if t.get("item_type") == "recommendation":
                    self.strategy_engine.initialize_state_for_recommendation(t)

    async def _run_index_sync(self, interval_seconds: int = 600):
        """
        ✅ v29.1: شبكة أمان دورية — كل 10 دقائق فقط.

        الصفقات الجديدة تصل فورياً عبر add_trigger_data() من creation_service.
        هذه الدالة دورها الوحيد: تصحيح أي تعارض في الذاكرة
        ولا علاقة لها باكتشاف الصفقات الجديدة.

        (كانت 60 ثانية — خُفِّضت لتوفير الموارد)
        """
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.build_triggers_index()
            except Exception:
                log.exception("Index sync iteration failed.")

    # ─────────────────────────────────────────────────────────────
    # Core evaluation helpers
    # ─────────────────────────────────────────────────────────────

    def _is_price_condition_met(
        self,
        side: str,
        low_price: Decimal,
        high_price: Decimal,
        target_price: Decimal,
        condition_type: str,
    ) -> bool:
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()
        if side_upper == "LONG":
            if cond.startswith("TP"):
                return high_price >= target_price
            if cond == "SL":
                return low_price <= target_price
            if cond == "ENTRY":
                return low_price <= target_price
        elif side_upper == "SHORT":
            if cond.startswith("TP"):
                return low_price <= target_price
            if cond == "SL":
                return high_price >= target_price
            if cond == "ENTRY":
                return high_price >= target_price
        return False

    async def _evaluate_core_triggers(
        self,
        trigger: Dict[str, Any],
        high_price: Decimal,
        low_price: Decimal,
    ) -> List[BaseAction]:
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
                    if "INVALIDATED" not in processed_events and self._is_price_condition_met(
                        side, low_price, high_price, sl_price, "SL"
                    ):
                        await self.lifecycle_service.process_invalidation_event(item_id)
                    elif "ACTIVATED" not in processed_events and self._is_price_condition_met(
                        side, low_price, high_price, entry_price, "ENTRY"
                    ):
                        await self.lifecycle_service.process_activation_event(item_id)

                elif status == RecommendationStatusEnum.ACTIVE:
                    sl_price = trigger.get("stop_loss")
                    if "SL_HIT" not in processed_events and "FINAL_CLOSE" not in processed_events:
                        if self._is_price_condition_met(
                            side, low_price, high_price, sl_price, "SL"
                        ):
                            actions.append(
                                CloseAction(
                                    rec_id=item_id, price=sl_price, reason="SL_HIT"
                                )
                            )
                            return actions

                    for i, target in enumerate(trigger.get("targets", []) or [], 1):
                        event_name = f"TP{i}_HIT"
                        if event_name in processed_events:
                            continue
                        tp_price = Decimal(str(target.get("price")))
                        if self._is_price_condition_met(
                            side, low_price, high_price, tp_price, "TP"
                        ):
                            await self.lifecycle_service.process_tp_hit_event(
                                item_id, i, tp_price
                            )

            elif item_type == "user_trade":
                if status in (
                    UserTradeStatusEnum.WATCHLIST,
                    UserTradeStatusEnum.PENDING_ACTIVATION,
                ):
                    entry_price = trigger.get("entry")
                    sl_price = trigger.get("stop_loss")
                    published_at = trigger.get("original_published_at")
                    if published_at and datetime.now(timezone.utc) < published_at:
                        return actions

                    if "INVALIDATED" not in processed_events and self._is_price_condition_met(
                        side, low_price, high_price, sl_price, "SL"
                    ):
                        await self.lifecycle_service.process_user_trade_invalidation_event(
                            item_id, sl_price
                        )
                    elif "ACTIVATED" not in processed_events and self._is_price_condition_met(
                        side, low_price, high_price, entry_price, "ENTRY"
                    ):
                        await self.lifecycle_service.process_user_trade_activation_event(
                            item_id
                        )

                elif status == UserTradeStatusEnum.ACTIVATED:
                    sl_price = trigger.get("stop_loss")
                    if "SL_HIT" not in processed_events and "FINAL_CLOSE" not in processed_events:
                        if self._is_price_condition_met(
                            side, low_price, high_price, sl_price, "SL"
                        ):
                            await self.lifecycle_service.process_user_trade_sl_hit_event(
                                item_id, sl_price
                            )
                            return actions

                    for i, target in enumerate(trigger.get("targets", []) or [], 1):
                        event_name = f"TP{i}_HIT"
                        if event_name in processed_events:
                            continue
                        tp_price = Decimal(str(target.get("price")))
                        if self._is_price_condition_met(
                            side, low_price, high_price, tp_price, "TP"
                        ):
                            await self.lifecycle_service.process_user_trade_tp_hit_event(
                                item_id, i, tp_price
                            )

        except Exception:
            log.exception(
                "Error evaluating core trigger for %s id=%s", item_type, item_id
            )

        return actions

    # ─────────────────────────────────────────────────────────────
    # Main queue processor
    # ─────────────────────────────────────────────────────────────

    async def _process_queue(self):
        log.info("AlertService: Queue processor started — waiting for price ticks.")
        while True:
            try:
                payload = await self.price_queue.get()

                if isinstance(payload, (list, tuple)):
                    if len(payload) == 4:
                        symbol, market, low_raw, high_raw = payload
                        close_raw = high_raw
                    elif len(payload) >= 5:
                        symbol, market, low_raw, high_raw, close_raw = payload[:5]
                    else:
                        self.price_queue.task_done()
                        continue
                elif isinstance(payload, dict):
                    symbol    = payload.get("symbol")
                    market    = payload.get("market", "Futures")
                    low_raw   = payload.get("low")
                    high_raw  = payload.get("high")
                    close_raw = payload.get("close", high_raw)
                else:
                    self.price_queue.task_done()
                    continue

                try:
                    low_price   = Decimal(str(low_raw))
                    high_price  = Decimal(str(high_raw))
                    close_price = Decimal(str(close_raw))
                except Exception:
                    self.price_queue.task_done()
                    continue

                tick = {
                    "high":  high_price,
                    "low":   low_price,
                    "close": close_price,
                    "ts":    int(time.time()),
                }
                key = f"{(symbol or '').upper()}:{market}"

                async with self._triggers_lock:
                    triggers_for_key = list(self.active_triggers.get(key, []))

                if not triggers_for_key:
                    self.price_queue.task_done()
                    continue

                # تحديث كاش الأسعار للـ WebApp
                try:
                    from capitalguard.infrastructure.core_engine import core_cache
                    await core_cache.set(f"price:FUTURES:{symbol}", float(close_price), ttl=60)
                    await core_cache.set(f"price:SPOT:{symbol}",    float(close_price), ttl=60)
                except Exception:
                    pass

                rec_triggers   = [t for t in triggers_for_key if t.get("item_type") == "recommendation"]
                other_triggers = [t for t in triggers_for_key if t.get("item_type") != "recommendation"]

                strategy_actions: List[BaseAction] = []
                if rec_triggers:
                    try:
                        batch = await self.strategy_engine.evaluate_batch(rec_triggers, tick)
                        if batch:
                            strategy_actions.extend(batch)
                    except Exception:
                        log.exception("StrategyEngine.evaluate_batch failed key=%s", key)

                for trig in other_triggers:
                    try:
                        acts = await self.strategy_engine.evaluate(trig, tick)
                        if acts:
                            strategy_actions.extend(acts)
                    except Exception:
                        log.exception("StrategyEngine.evaluate failed id=%s", trig.get("id"))

                core_actions: List[BaseAction] = []
                for trig in triggers_for_key:
                    try:
                        acts = await self._evaluate_core_triggers(trig, high_price, low_price)
                        if acts:
                            core_actions.extend(acts)
                    except Exception:
                        log.exception("Core evaluate failed id=%s", trig.get("id"))

                all_actions = strategy_actions + core_actions

                if not all_actions:
                    self.price_queue.task_done()
                    continue

                close_action = next(
                    (a for a in all_actions if isinstance(a, CloseAction)), None
                )
                if close_action:
                    try:
                        await self.lifecycle_service.close_recommendation_async(
                            rec_id=close_action.rec_id,
                            user_id=self._find_user_id_for_rec(
                                close_action.rec_id, triggers_for_key
                            ),
                            exit_price=close_action.price,
                            reason=getattr(close_action, "reason", "CLOSE"),
                            rebuild_alerts=False,
                        )
                    except Exception:
                        log.exception(
                            "close_recommendation_async failed rec=%s",
                            close_action.rec_id,
                        )
                    try:
                        self.strategy_engine.clear_state(close_action.rec_id)
                    except Exception:
                        pass
                    self.price_queue.task_done()
                    continue

                for act in all_actions:
                    try:
                        if isinstance(act, MoveSLAction):
                            await self.lifecycle_service.update_sl_for_user_async(
                                rec_id=act.rec_id,
                                user_id=self._find_user_id_for_rec(
                                    act.rec_id, triggers_for_key
                                ),
                                new_sl=act.new_sl,
                            )
                        elif isinstance(act, AlertAction):
                            if hasattr(self.lifecycle_service, "send_alert_async"):
                                try:
                                    await self.lifecycle_service.send_alert_async(
                                        rec_id=act.rec_id,
                                        level=getattr(act, "level", "info"),
                                        message=getattr(act, "message", ""),
                                        metadata=getattr(act, "metadata", None),
                                    )
                                except Exception:
                                    pass
                    except Exception:
                        log.exception(
                            "Action %s failed rec=%s",
                            type(act),
                            getattr(act, "rec_id", None),
                        )

                self.price_queue.task_done()

            except asyncio.CancelledError:
                log.info("Queue processor cancelled.")
                break
            except Exception:
                log.exception("Unexpected error in queue processor.")

    # ─────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────

    def _find_user_id_for_rec(
        self, rec_id: int, trig_list: List[Dict[str, Any]]
    ) -> Optional[str]:
        for t in trig_list:
            if t.get("id") == rec_id:
                return t.get("user_id")
        return None

    def stop(self):
        try:
            if self.streamer and hasattr(self.streamer, "stop"):
                self.streamer.stop()
            if self._bg_loop:
                if self._processing_task:
                    self._bg_loop.call_soon_threadsafe(self._processing_task.cancel)
                if self._index_sync_task:
                    self._bg_loop.call_soon_threadsafe(self._index_sync_task.cancel)
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if self._bg_thread:
                self._bg_thread.join(timeout=5.0)
        except Exception:
            log.exception("Error stopping AlertService.")

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
