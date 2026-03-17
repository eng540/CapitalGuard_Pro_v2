# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
# File: src/capitalguard/application/services/alert_service.py
# Version: v30.0-PARTITIONED
#
# ✅ THE UPGRADE — Partitioned Processing (معالجة مُقسَّمة لكل رمز):
#
# المشكلة في v29:
#   queue واحدة + معالج واحد sequential:
#   BTC يضرب SL → يستغرق 1-2 ثانية (DB + Telegram)
#   ETH/SOL/XRP تنتظر في الـ queue بلا داعٍ
#
# الهيكل الجديد — طبقتان:
#
#   الطبقة 1: price_queue (Router)
#     تستقبل كل التيكات من PriceStreamer
#     تُوجِّهها فوراً لـ queue الرمز المناسب
#     لا تقوم بأي معالجة — router فقط
#
#   الطبقة 2: _symbol_queues[key] + _symbol_workers[key]
#     queue منفصلة لكل رمز: "BTCUSDT:Futures", "ETHUSDT:Futures", ...
#     worker منفصل لكل رمز يعمل باستقلالية تامة
#     BTC يضرب SL؟ ETH وSOL لا يتأثران أبداً
#
# النتيجة:
#   قبل: 1 queue × N رمز = رمز واحد يحجب الباقين
#   بعد: N queue × N worker = كل رمز في مساره المستقل
#
# ✅ محفوظ من v29.0:
#   FIX-A, FIX-B, FIX-C (price_queue, triggers_lock, PriceStreamer)
#   كل منطق evaluation (SL/TP/ENTRY) بدون تغيير
#   PriceStreamer interface بدون تغيير
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-17

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

# ── Constants ──────────────────────────────────────────────────────────────
# حجم الـ routing queue (الطبقة 1)
ROUTER_QUEUE_SIZE = 5_000

# حجم كل symbol queue (الطبقة 2)
# كل رمز يحصل على تيك واحد في الثانية → 10 تيكات كافية
SYMBOL_QUEUE_SIZE = 10


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
    AlertService v30 — Partitioned Processing.

    Architecture:
      Binance WS → PriceStreamer → price_queue (Router)
          ↓
      _route_ticks() → symbol_queues["BTCUSDT:Futures"]
                     → symbol_queues["ETHUSDT:Futures"]
                     → symbol_queues["SOLUSDT:Futures"]
          ↓              ↓              ↓
      worker_BTC     worker_ETH     worker_SOL
      (مستقل)        (مستقل)        (مستقل)
          ↓
      lifecycle → DB + Telegram
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

        self._streamer_arg = streamer

        # ── Tier 1: Router Queue ───────────────────────────────────────────
        # تستقبل كل التيكات — PriceStreamer يكتب هنا (interface بدون تغيير)
        self.price_queue: Optional[asyncio.Queue] = None

        # ── Tier 2: Per-Symbol Queues + Workers ────────────────────────────
        # يُنشأ ديناميكياً عند أول تيك لكل رمز
        self._symbol_queues: Dict[str, asyncio.Queue] = {}
        self._symbol_workers: Dict[str, asyncio.Task] = {}

        # ── Thread Safety ─────────────────────────────────────────────────
        self._sync_lock = threading.RLock()
        self._triggers_lock: Optional[asyncio.Lock] = None
        self._workers_lock: Optional[asyncio.Lock] = None  # لحماية _symbol_workers

        # ── Triggers Index ────────────────────────────────────────────────
        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}

        # ── Tasks ─────────────────────────────────────────────────────────
        self._routing_task: Optional[asyncio.Task] = None    # Router (Tier 1)
        self._index_sync_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self.streamer: Optional[PriceStreamer] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Background runner
    # ─────────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._bg_thread and self._bg_thread.is_alive():
            log.warning("AlertService already running.")
            return

        def _bg_runner():
            try:
                loop = asyncio.new_event_loop()
                self._bg_loop = loop
                asyncio.set_event_loop(loop)

                # ── Tier 1: Router Queue ───────────────────────────────────
                self.price_queue = asyncio.Queue(maxsize=ROUTER_QUEUE_SIZE)
                self._triggers_lock = asyncio.Lock()
                self._workers_lock = asyncio.Lock()
                log.info("AlertService: queues and locks created.")

                # ── PriceStreamer ──────────────────────────────────────────
                if self._streamer_arg is not None:
                    self.streamer = self._streamer_arg
                else:
                    self.streamer = PriceStreamer(self.price_queue, self.repo)
                    log.info("AlertService: PriceStreamer created.")

                # ✅ P1-FIX: نُمرِّر مرجع active_triggers للـ PriceStreamer
                # يُستخدم في Safety Sweep لمعرفة الرموز النشطة بدون DB
                if hasattr(self.streamer, "set_active_triggers_ref"):
                    self.streamer.set_active_triggers_ref(self.active_triggers)

                # ── Tasks ──────────────────────────────────────────────────
                # Router يقرأ من price_queue ويُوجِّه لـ symbol queues
                self._routing_task   = loop.create_task(self._route_ticks())
                self._index_sync_task = loop.create_task(self._run_index_sync())

                # ── PriceStreamer start ────────────────────────────────────
                try:
                    self.streamer.start(loop=loop)
                    log.info("AlertService: PriceStreamer started.")
                except TypeError:
                    self.streamer.start()
                except Exception as e:
                    log.error("AlertService: PriceStreamer start failed: %s", e)

                # ── نقل triggers مُحمَّلة مسبقاً ──────────────────────────
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

                log.info(
                    "AlertService v30 started — "
                    "Partitioned Processing active."
                )
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

    # ─────────────────────────────────────────────────────────────────────────
    # Tier 1 — Router
    # ─────────────────────────────────────────────────────────────────────────

    async def _route_ticks(self) -> None:
        """
        الطبقة 1: يقرأ من price_queue ويُوجِّه كل تيك
        لـ queue الرمز المناسب في الطبقة 2.
        لا يقوم بأي معالجة — router خالص.
        """
        log.info("AlertService: Tick Router started.")
        while True:
            try:
                payload = await self.price_queue.get()

                # ── استخراج الرمز ──────────────────────────────────────
                if isinstance(payload, dict):
                    symbol = payload.get("symbol", "")
                    market = payload.get("market", "Futures")
                elif isinstance(payload, (list, tuple)) and len(payload) >= 2:
                    symbol = payload[0]
                    market = payload[1] if len(payload) > 1 else "Futures"
                else:
                    self.price_queue.task_done()
                    continue

                key = f"{(symbol or '').upper()}:{market}"

                # ── تحديث cache للـ WebApp (هنا في Router لا في Worker) ──
                try:
                    from capitalguard.infrastructure.core_engine import core_cache
                    close_raw = (
                        payload.get("close") if isinstance(payload, dict)
                        else (payload[4] if len(payload) > 4 else payload[3])
                    )
                    if close_raw:
                        await core_cache.set(
                            f"price:FUTURES:{symbol}", float(close_raw), ttl=60
                        )
                        await core_cache.set(
                            f"price:SPOT:{symbol}", float(close_raw), ttl=60
                        )
                except Exception:
                    pass

                # ── تحقق من وجود triggers لهذا الرمز ─────────────────
                async with self._triggers_lock:
                    has_triggers = bool(self.active_triggers.get(key))

                if not has_triggers:
                    self.price_queue.task_done()
                    continue

                # ── توجيه للـ Symbol Worker ────────────────────────────
                await self._dispatch_to_symbol(key, payload)
                self.price_queue.task_done()

            except asyncio.CancelledError:
                log.info("AlertService: Tick Router cancelled.")
                break
            except Exception:
                log.exception("AlertService: Router unexpected error.")

    async def _dispatch_to_symbol(self, key: str, payload: Any) -> None:
        """
        يُرسل التيك لـ queue الرمز.
        إذا لم يوجد worker → يُنشئه.
        إذا امتلأ الـ queue → يتجاهل التيك القديم ويضع الجديد.
        """
        async with self._workers_lock:
            # إنشاء queue + worker عند الحاجة
            if key not in self._symbol_queues:
                q = asyncio.Queue(maxsize=SYMBOL_QUEUE_SIZE)
                self._symbol_queues[key] = q
                task = asyncio.ensure_future(self._symbol_worker(key, q))
                self._symbol_workers[key] = task
                log.debug("AlertService: created worker for %s", key)

            q = self._symbol_queues[key]

        # ── وضع التيك — non-blocking ────────────────────────────────────
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # الـ queue ممتلئة → تجاهل أقدم تيك واستبدله بالجديد
            # التيك القديم بيانات منتهية الصلاحية — الجديد أدق
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # لا يحدث عملياً

    # ─────────────────────────────────────────────────────────────────────────
    # Tier 2 — Per-Symbol Worker
    # ─────────────────────────────────────────────────────────────────────────

    async def _symbol_worker(self, key: str, queue: asyncio.Queue) -> None:
        """
        الطبقة 2: معالج مستقل لرمز واحد.
        BTC worker و ETH worker يعملان بالتوازي — لا يحجب أحدهما الآخر.
        """
        log.debug("AlertService: worker started for %s", key)
        while True:
            try:
                payload = await queue.get()

                # ── استخراج بيانات التيك ──────────────────────────────
                if isinstance(payload, (list, tuple)):
                    if len(payload) == 4:
                        symbol, market, low_raw, high_raw = payload
                        close_raw = high_raw
                    elif len(payload) >= 5:
                        symbol, market, low_raw, high_raw, close_raw = payload[:5]
                    else:
                        queue.task_done()
                        continue
                elif isinstance(payload, dict):
                    symbol    = payload.get("symbol")
                    market    = payload.get("market", "Futures")
                    low_raw   = payload.get("low")
                    high_raw  = payload.get("high")
                    close_raw = payload.get("close", high_raw)
                else:
                    queue.task_done()
                    continue

                try:
                    low_price   = Decimal(str(low_raw))
                    high_price  = Decimal(str(high_raw))
                    close_price = Decimal(str(close_raw))
                except Exception:
                    queue.task_done()
                    continue

                tick = {
                    "high":  high_price,
                    "low":   low_price,
                    "close": close_price,
                    "ts":    int(time.time()),
                }

                # ── snapshot triggers لهذا الرمز ──────────────────────
                async with self._triggers_lock:
                    triggers_for_key = list(self.active_triggers.get(key, []))

                if not triggers_for_key:
                    # لا توصيات نشطة → نظّف الـ worker
                    queue.task_done()
                    await self._cleanup_worker(key)
                    return

                # ── Evaluation ────────────────────────────────────────
                rec_triggers   = [t for t in triggers_for_key if t.get("item_type") == "recommendation"]
                other_triggers = [t for t in triggers_for_key if t.get("item_type") != "recommendation"]

                strategy_actions: List[BaseAction] = []
                if rec_triggers:
                    try:
                        batch = await self.strategy_engine.evaluate_batch(rec_triggers, tick)
                        if batch:
                            strategy_actions.extend(batch)
                    except Exception:
                        log.exception("evaluate_batch failed key=%s", key)

                for trig in other_triggers:
                    try:
                        acts = await self.strategy_engine.evaluate(trig, tick)
                        if acts:
                            strategy_actions.extend(acts)
                    except Exception:
                        log.exception("evaluate failed id=%s", trig.get("id"))

                core_actions: List[BaseAction] = []
                for trig in triggers_for_key:
                    try:
                        acts = await self._evaluate_core_triggers(trig, high_price, low_price)
                        if acts:
                            core_actions.extend(acts)
                    except Exception:
                        log.exception("core_evaluate failed id=%s", trig.get("id"))

                all_actions = strategy_actions + core_actions

                if not all_actions:
                    queue.task_done()
                    continue

                # ── تنفيذ Actions ─────────────────────────────────────
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
                        log.exception("close_recommendation_async failed rec=%s", close_action.rec_id)
                    try:
                        self.strategy_engine.clear_state(close_action.rec_id)
                    except Exception:
                        pass
                    queue.task_done()
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
                        log.exception("Action %s failed rec=%s", type(act), getattr(act, "rec_id", None))

                queue.task_done()

            except asyncio.CancelledError:
                log.debug("AlertService: worker cancelled for %s", key)
                break
            except Exception:
                log.exception("AlertService: unexpected error in worker for %s", key)

    async def _cleanup_worker(self, key: str) -> None:
        """يُزيل worker رمز ليس له triggers نشطة."""
        async with self._workers_lock:
            self._symbol_queues.pop(key, None)
            task = self._symbol_workers.pop(key, None)
            if task and not task.done():
                task.cancel()
        log.debug("AlertService: worker cleaned up for %s", key)

    # ─────────────────────────────────────────────────────────────────────────
    # Index builders — thread-safe
    # ─────────────────────────────────────────────────────────────────────────

    def build_trigger_data_from_orm(
        self, item_orm: Union[Recommendation, UserTrade]
    ) -> Optional[Dict[str, Any]]:
        """تحويل ORM إلى trigger dict."""
        try:
            if isinstance(item_orm, Recommendation):
                rec = item_orm
                entry_dec = _to_decimal(rec.entry)
                sl_dec    = _to_decimal(rec.stop_loss)
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
                        e.event_type for e in (getattr(rec, "events", []) or [])
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
                sl_dec    = _to_decimal(trade.stop_loss)
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
                        e.event_type for e in (getattr(trade, "events", []) or [])
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

    async def add_trigger_data(self, item_data: Dict[str, Any]) -> None:
        if not item_data:
            return
        item_id   = item_data.get("id")
        item_type = item_data.get("item_type")
        asset     = item_data.get("asset")
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
        lst = self.active_triggers.setdefault(key, [])
        if not any(t["id"] == item_id and t["item_type"] == item_type for t in lst):
            lst.append(item_data)
            if item_type == "recommendation":
                self.strategy_engine.initialize_state_for_recommendation(item_data)

    async def remove_single_trigger(self, item_type: str, item_id: int) -> None:
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

    async def build_triggers_index(self) -> None:
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
            "AlertService: index built — %d symbols, %d triggers.",
            len(new_index), total,
        )

    def _apply_new_index(self, new_index: Dict) -> None:
        self.active_triggers = new_index
        self.strategy_engine.clear_all_states()
        for triggers in new_index.values():
            for t in triggers:
                if t.get("item_type") == "recommendation":
                    self.strategy_engine.initialize_state_for_recommendation(t)

    async def _run_index_sync(self, interval_seconds: int = 600) -> None:
        """شبكة أمان — كل 10 دقائق فقط."""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.build_triggers_index()
            except Exception:
                log.exception("Index sync iteration failed.")

    # ─────────────────────────────────────────────────────────────────────────
    # Core evaluation helpers (بدون تغيير)
    # ─────────────────────────────────────────────────────────────────────────

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
            if cond.startswith("TP"):  return high_price >= target_price
            if cond == "SL":           return low_price  <= target_price
            if cond == "ENTRY":        return low_price  <= target_price
        elif side_upper == "SHORT":
            if cond.startswith("TP"):  return low_price  <= target_price
            if cond == "SL":           return high_price >= target_price
            if cond == "ENTRY":        return high_price >= target_price
        return False

    async def _evaluate_core_triggers(
        self,
        trigger: Dict[str, Any],
        high_price: Decimal,
        low_price: Decimal,
    ) -> List[BaseAction]:
        actions: List[BaseAction] = []
        item_id         = trigger.get("id")
        status          = trigger.get("status")
        side            = trigger.get("side")
        item_type       = trigger.get("item_type", "recommendation")
        processed_events = trigger.get("processed_events", set())

        try:
            if item_type == "recommendation":
                if status == RecommendationStatusEnum.PENDING:
                    entry_price = trigger.get("entry")
                    sl_price    = trigger.get("stop_loss")
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
                        if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                            actions.append(CloseAction(rec_id=item_id, price=sl_price, reason="SL_HIT"))
                            return actions

                    for i, target in enumerate(trigger.get("targets", []) or [], 1):
                        if f"TP{i}_HIT" in processed_events:
                            continue
                        tp_price = Decimal(str(target.get("price")))
                        if self._is_price_condition_met(side, low_price, high_price, tp_price, "TP"):
                            await self.lifecycle_service.process_tp_hit_event(item_id, i, tp_price)

            elif item_type == "user_trade":
                if status in (UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION):
                    entry_price  = trigger.get("entry")
                    sl_price     = trigger.get("stop_loss")
                    published_at = trigger.get("original_published_at")
                    if published_at and datetime.now(timezone.utc) < published_at:
                        return actions

                    if "INVALIDATED" not in processed_events and self._is_price_condition_met(
                        side, low_price, high_price, sl_price, "SL"
                    ):
                        await self.lifecycle_service.process_user_trade_invalidation_event(item_id, sl_price)
                    elif "ACTIVATED" not in processed_events and self._is_price_condition_met(
                        side, low_price, high_price, entry_price, "ENTRY"
                    ):
                        await self.lifecycle_service.process_user_trade_activation_event(item_id)

                elif status == UserTradeStatusEnum.ACTIVATED:
                    sl_price = trigger.get("stop_loss")
                    if "SL_HIT" not in processed_events and "FINAL_CLOSE" not in processed_events:
                        if self._is_price_condition_met(side, low_price, high_price, sl_price, "SL"):
                            await self.lifecycle_service.process_user_trade_sl_hit_event(item_id, sl_price)
                            return actions

                    for i, target in enumerate(trigger.get("targets", []) or [], 1):
                        if f"TP{i}_HIT" in processed_events:
                            continue
                        tp_price = Decimal(str(target.get("price")))
                        if self._is_price_condition_met(side, low_price, high_price, tp_price, "TP"):
                            await self.lifecycle_service.process_user_trade_tp_hit_event(item_id, i, tp_price)

        except Exception:
            log.exception("Error evaluating trigger for %s id=%s", item_type, item_id)

        return actions

    # ─────────────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────────────

    def _find_user_id_for_rec(
        self, rec_id: int, trig_list: List[Dict[str, Any]]
    ) -> Optional[str]:
        for t in trig_list:
            if t.get("id") == rec_id:
                return t.get("user_id")
        return None

    def stop(self) -> None:
        try:
            if self.streamer and hasattr(self.streamer, "stop"):
                self.streamer.stop()
            if self._bg_loop:
                # إلغاء Router
                if self._routing_task:
                    self._bg_loop.call_soon_threadsafe(self._routing_task.cancel)
                if self._index_sync_task:
                    self._bg_loop.call_soon_threadsafe(self._index_sync_task.cancel)
                # إلغاء كل symbol workers
                for task in self._symbol_workers.values():
                    if not task.done():
                        self._bg_loop.call_soon_threadsafe(task.cancel)
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if self._bg_thread:
                self._bg_thread.join(timeout=5.0)
        except Exception:
            log.exception("Error stopping AlertService.")

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
