# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/strategy/engine.py --- v4
"""
src/capitalguard/application/strategy/engine.py — StrategyEngine v4.0

ميزات رئيسية (v4):
- DI عبر lifecycle_service (تنفيذ الآثار الجانبية خارج المحرك).
- محرك "pure" ينتج قائمة Actions دون تنفيذها مباشرة.
- دعم evaluate_batch و evaluate (single).
- حالة داخلية قابلة للتسلسل (serialize/restore) للـ checkpointing.
- أقفال per-rec لضمان التزامن الآمن (asyncio.Lock).
- استراتيجيات: FIXED, TRAILING, BREAK_EVEN, TIME_BASED (قابلة للتمديد).
- hooks: on_action_generated, on_state_changed.
- دعم خيارات metrics و storage (اختياري).
- جميع عمليات الحساب بالـ Decimal للحفاظ على الدقة العددية.

المطلوبات قبل التشغيل:
- تمرير كائن lifecycle_service يوفّر واجهات التنفيذ عند الحاجة (لكن المحرك لا ينفذ أي أثر جانبي بنفسه).
- إذا رُغب بالـ persistence: تمرير storage مع واجهات get/set.
"""
from __future__ import annotations

import asyncio
import logging
import time
import json
from decimal import Decimal, getcontext, Context
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional, Callable, Iterable, Tuple

# Types for lifecycle_service and storage are loosely typed to avoid circular imports.
# lifecycle_service must implement side-effect methods (used externally by caller).
# storage is optional: expected methods get(key)->str|None, set(key, str)->None, delete(key)->None.
# metrics is optional: expected methods increment(name, value=1), gauge(name, value), timing(name, ms)
logger = logging.getLogger(__name__)

# Tune Decimal context for consistent precision across engine
DECIMAL_CONTEXT = Context(prec=28, rounding="ROUND_HALF_EVEN")
getcontext().prec = DECIMAL_CONTEXT.prec

# --- Action data classes ---
@dataclass(frozen=True)
class BaseAction:
    rec_id: int
    # prevent engine_version from becoming an __init__ parameter to avoid dataclass ordering issues
    engine_version: str = field(init=False, default="v4")

@dataclass(frozen=True)
class CloseAction(BaseAction):
    price: Decimal
    reason: str
    metadata: Optional[Dict[str, Any]] = None

@dataclass(frozen=True)
class MoveSLAction(BaseAction):
    new_sl: Decimal
    metadata: Optional[Dict[str, Any]] = None

@dataclass(frozen=True)
class AlertAction(BaseAction):
    level: str
    message: str
    metadata: Optional[Dict[str, Any]] = None

Action = Any  # Union[CloseAction, MoveSLAction, AlertAction] — kept Any for simpler typing across files

# --- Internal state model per recommendation ---
# Stored values must be JSON-serializable via to_serializable_state()
class _EngineStateItem:
    def __init__(self, rec_id: int, entry: Decimal, ts: Optional[int] = None):
        self.rec_id = int(rec_id)
        self.highest: Decimal = Decimal(entry) if entry is not None else Decimal("0")
        self.lowest: Decimal = Decimal(entry) if entry is not None else Decimal("0")
        self.in_profit_zone: bool = False
        self.last_trailing_sl: Optional[Decimal] = None
        self.last_tick_ts: Optional[int] = ts
        self.initialized_at: int = int(time.time())

    def to_serializable(self) -> Dict[str, Any]:
        return {
            "rec_id": self.rec_id,
            "highest": str(self.highest),
            "lowest": str(self.lowest),
            "in_profit_zone": self.in_profit_zone,
            "last_trailing_sl": str(self.last_trailing_sl) if self.last_trailing_sl is not None else None,
            "last_tick_ts": self.last_tick_ts,
            "initialized_at": self.initialized_at,
        }

    @classmethod
    def from_serializable(cls, data: Dict[str, Any]) -> "_EngineStateItem":
        obj = cls(int(data["rec_id"]), Decimal(data.get("highest", "0")), ts=data.get("last_tick_ts"))
        obj.highest = Decimal(data.get("highest", "0"))
        obj.lowest = Decimal(data.get("lowest", "0"))
        obj.in_profit_zone = bool(data.get("in_profit_zone", False))
        obj.last_trailing_sl = Decimal(data["last_trailing_sl"]) if data.get("last_trailing_sl") is not None else None
        obj.last_tick_ts = data.get("last_tick_ts")
        obj.initialized_at = data.get("initialized_at", int(time.time()))
        return obj


# --- Strategy Engine ---
class StrategyEngine:
    """
    StrategyEngine (v4):
    - Pure evaluator: returns Actions and does not perform side-effects.
    - Thread-safe for asyncio usage via per-rec asyncio.Lock.
    """

    def __init__(
        self,
        lifecycle_service: Any,
        *,
        storage: Optional[Any] = None,
        metrics: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        """
        Args:
            lifecycle_service: injected service (used externally by caller to apply actions).
            storage: optional persistence (expects get/set/delete).
            metrics: optional metrics sink (increment, gauge, timing).
            config: engine tuning options (thresholds, heuristics).
        """
        self.lifecycle_service = lifecycle_service
        self._state: Dict[int, _EngineStateItem] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
        self._hooks: Dict[str, List[Callable[..., Any]]] = {}
        self.storage = storage
        self.metrics = metrics
        self.config = config or {}
        self.engine_version = "v4"
        logger.info("StrategyEngine v4 initialized")

    # --- Hook management ---
    def register_hook(self, name: str, fn: Callable[..., Any]) -> None:
        """Register callback hooks: 'on_action_generated', 'on_state_changed'"""
        if name not in self._hooks:
            self._hooks[name] = []
        self._hooks[name].append(fn)
        logger.debug("Registered hook %s -> %s", name, fn)

    def _emit_hook(self, name: str, *args, **kwargs):
        for fn in self._hooks.get(name, []):
            try:
                fn(*args, **kwargs)
            except Exception as e:
                logger.exception("Hook %s raised: %s", name, e)

    # --- Lock helpers ---
    def _get_lock(self, rec_id: int) -> asyncio.Lock:
        if rec_id not in self._locks:
            self._locks[rec_id] = asyncio.Lock()
        return self._locks[rec_id]

    # --- State serialization ---
    def serialize_state(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict representing the engine state."""
        return {
            "engine_version": self.engine_version,
            "items": {str(rec_id): item.to_serializable() for rec_id, item in self._state.items()}
        }

    def restore_state(self, blob: Dict[str, Any]) -> None:
        """Restore engine state from serialized blob (as returned by serialize_state)."""
        items = blob.get("items", {})
        self._state.clear()
        for rec_id_str, item_data in items.items():
            try:
                obj = _EngineStateItem.from_serializable(item_data)
                self._state[int(rec_id_str)] = obj
            except Exception:
                logger.exception("Failed to restore state for rec %s", rec_id_str)
        logger.info("State restored: %d items", len(self._state))

    async def persist_state(self, key: str) -> None:
        """Persist current serialized state via storage if provided."""
        if not self.storage:
            return
        try:
            blob = self.serialize_state()
            self.storage.set(key, json.dumps(blob))
            logger.debug("Persisted engine state to storage key=%s", key)
        except Exception:
            logger.exception("Failed to persist engine state")

    async def load_persisted_state(self, key: str) -> None:
        """Load persisted state from storage if present."""
        if not self.storage:
            return
        try:
            raw = self.storage.get(key)
            if not raw:
                return
            blob = json.loads(raw)
            self.restore_state(blob)
            logger.info("Loaded persisted state from key=%s", key)
        except Exception:
            logger.exception("Failed to load persisted engine state")

    # --- Public state operations ---
    def initialize_state_for_recommendation(self, rec_dict: Dict[str, Any]) -> None:
        rec_id = int(rec_dict["id"])
        entry = rec_dict.get("entry", "0")
        ts = rec_dict.get("created_at") or int(time.time())
        self._state[rec_id] = _EngineStateItem(rec_id, Decimal(str(entry)), ts=ts)
        logger.debug("Initialized state for rec #%d", rec_id)
        self._emit_hook("on_state_changed", rec_id, self._state[rec_id].to_serializable())

    def clear_state(self, rec_id: int) -> None:
        if rec_id in self._state:
            self._state.pop(rec_id, None)
            logger.debug("Cleared state for rec #%d", rec_id)
            self._emit_hook("on_state_changed", rec_id, None)

    def clear_all_states(self) -> None:
        self._state.clear()
        logger.debug("Cleared all engine states")
        self._emit_hook("on_state_changed", None, None)

    def rebuild_index(self, recs: Iterable[Dict[str, Any]]) -> None:
        """Rebuilds internal state from an iterable of recommendation dicts."""
        self._state.clear()
        for rec in recs:
            try:
                if rec.get("status") and str(rec.get("status")).upper().endswith("ACTIVE"):
                    self.initialize_state_for_recommendation(rec)
            except Exception:
                logger.exception("Failed to initialize state for rec in rebuild: %s", rec.get("id"))
        logger.info("Rebuild index completed. states=%d", len(self._state))

    # --- Evaluation API ---
    async def evaluate_batch(self, recs: Iterable[Dict[str, Any]], tick: Dict[str, Any]) -> List[Action]:
        """
        Evaluate a batch of recommendations against a single tick.
        tick: {"high": Decimal|str|float, "low": Decimal|str|float, "close": Decimal|str|float, "ts": int}
        Returns list of Actions.
        """
        # Normalize tick
        high = Decimal(str(tick.get("high", "0")))
        low = Decimal(str(tick.get("low", "0")))
        close = Decimal(str(tick.get("close", "0")))
        ts = int(tick.get("ts", int(time.time())))

        actions: List[Action] = []
        # Evaluate sequentially but collect actions first to avoid partial side-effects
        for rec in recs:
            try:
                rec_id = int(rec["id"])
            except Exception:
                continue
            # Use per-rec lock to prevent races when evaluate_batch is used concurrently
            lock = self._get_lock(rec_id)
            async with lock:
                new_actions = self._evaluate_single_locked(rec, high, low, close, ts)
                if new_actions:
                    actions.extend(new_actions)
                    for act in new_actions:
                        self._emit_hook("on_action_generated", act)
                        if self.metrics:
                            try:
                                self.metrics.increment("strategy.actions_generated_total", 1)
                                self.metrics.increment(f"strategy.actions_by_type.{act.__class__.__name__}", 1)
                            except Exception:
                                logger.debug("Metric increment failed for action metrics", exc_info=False)
        return actions

    async def evaluate(self, rec: Dict[str, Any], tick: Dict[str, Any]) -> List[Action]:
        """
        Evaluate a single recommendation against tick.
        This acquires the per-rec lock and returns actions.
        """
        rec_id = int(rec["id"])
        lock = self._get_lock(rec_id)
        high = Decimal(str(tick.get("high", "0")))
        low = Decimal(str(tick.get("low", "0")))
        close = Decimal(str(tick.get("close", "0")))
        ts = int(tick.get("ts", int(time.time())))
        async with lock:
            actions = self._evaluate_single_locked(rec, high, low, close, ts)
            for act in actions:
                self._emit_hook("on_action_generated", act)
                if self.metrics:
                    try:
                        self.metrics.increment("strategy.actions_generated_total", 1)
                        self.metrics.increment(f"strategy.actions_by_type.{act.__class__.__name__}", 1)
                    except Exception:
                        logger.debug("Metric increment failed for action metrics", exc_info=False)
            return actions

    # --- Core single-evaluation logic (expects lock to be held) ---
    def _evaluate_single_locked(self, rec: Dict[str, Any], high: Decimal, low: Decimal, close: Decimal, ts: int) -> List[Action]:
        actions: List[Action] = []
        # Basic validation and eligibility
        if not rec or str(rec.get("status", "")).upper() != "ACTIVE" or not rec.get("profit_stop_active", False):
            return actions

        rec_id = int(rec["id"])
        # ensure state exists
        if rec_id not in self._state:
            self.initialize_state_for_recommendation(rec)
        state = self._state[rec_id]

        # Update last tick ts
        state.last_tick_ts = ts

        side = str(rec.get("side", "LONG")).upper()
        mode = str(rec.get("profit_stop_mode", "NONE")).upper()

        # Update highest/lowest tracked
        if side == "LONG":
            if high > state.highest:
                state.highest = Decimal(high)
        else:  # SHORT
            if low < state.lowest:
                state.lowest = Decimal(low)

        # Emit state change hook
        self._emit_hook("on_state_changed", rec_id, state.to_serializable())

        # Strategy dispatch
        if mode == "FIXED":
            act = self._handle_fixed_profit_stop(rec, high, low, state)
            if act: actions.append(act)
        elif mode == "TRAILING":
            act = self._handle_trailing_stop(rec, state)
            if act:
                actions.append(act)
                # update last_trailing_sl on successful potential move to avoid repeated identical moves
                if isinstance(act, MoveSLAction):
                    state.last_trailing_sl = act.new_sl
                    self._emit_hook("on_state_changed", rec_id, state.to_serializable())
        elif mode == "BREAK_EVEN":
            act = self._handle_break_even(rec, state)
            if act: actions.append(act)
        elif mode == "TIME_BASED":
            act = self._handle_time_based(rec, state, ts)
            if act: actions.append(act)

        return actions

    # --- Strategy implementations ---
    def _handle_fixed_profit_stop(self, rec: Dict[str, Any], high: Decimal, low: Decimal, state: _EngineStateItem) -> Optional[CloseAction]:
        profit_price_raw = rec.get("profit_stop_price")
        if profit_price_raw is None:
            return None
        profit_price = Decimal(str(profit_price_raw))
        rec_id = int(rec["id"])
        side = str(rec.get("side", "LONG")).upper()

        # Enter profit zone when candle touches profit_price
        if not state.in_profit_zone:
            if (side == "LONG" and high >= profit_price) or (side == "SHORT" and low <= profit_price):
                state.in_profit_zone = True
                logger.info("Rec #%d entered profit zone at %s (FIXED)", rec_id, str(profit_price))
                self._emit_hook("on_state_changed", rec_id, state.to_serializable())
                return None

        # If in zone, trigger close on retracement to or beyond profit_price
        if state.in_profit_zone:
            if (side == "LONG" and low <= profit_price) or (side == "SHORT" and high >= profit_price):
                logger.info("FIXED profit stop hit for rec #%d at %s", rec_id, str(profit_price))
                return CloseAction(rec_id=rec_id, price=profit_price, reason="PROFIT_STOP_HIT", metadata={"engine": self.engine_version})

        return None

    def _handle_trailing_stop(self, rec: Dict[str, Any], state: _EngineStateItem) -> Optional[MoveSLAction]:
        # trailing_value may be percentage (<=10 heuristic) or absolute price distance
        trailing_raw = rec.get("profit_stop_trailing_value")
        if trailing_raw is None:
            return None

        current_sl_raw = rec.get("stop_loss")
        if current_sl_raw is None:
            return None
        current_sl = Decimal(str(current_sl_raw))
        trailing_value = Decimal(str(trailing_raw))

        side = str(rec.get("side", "LONG")).upper()
        rec_id = int(rec["id"])

        # Determine if trailing_value is percentage
        is_percentage = False
        heuristic_threshold = self.config.get("percentage_threshold", Decimal("10"))
        try:
            if trailing_value <= Decimal(str(heuristic_threshold)):
                is_percentage = True
        except Exception:
            is_percentage = False

        # reference price
        if side == "LONG":
            reference_price = state.highest
            distance = (reference_price * (trailing_value / Decimal("100"))) if is_percentage else trailing_value
            new_potential_sl = reference_price - distance
            # ensure not negative
            if new_potential_sl <= Decimal("0"):
                return None
            is_better = new_potential_sl > current_sl
        else:  # SHORT
            reference_price = state.lowest
            distance = (reference_price * (trailing_value / Decimal("100"))) if is_percentage else trailing_value
            new_potential_sl = reference_price + distance
            is_better = new_potential_sl < current_sl

        # Prevent tiny movements: require min_sl_move threshold
        min_move = Decimal(str(self.config.get("min_sl_move", "0")))
        if min_move and abs(new_potential_sl - (state.last_trailing_sl or current_sl)) <= Decimal(str(min_move)):
            logger.debug("Trailing SL move too small for rec #%d (delta <= %s)", rec_id, str(min_move))
            return None

        if is_better:
            logger.info("Trailing candidate for rec #%d: move SL %s -> %s", rec_id, str(current_sl), str(new_potential_sl))
            return MoveSLAction(rec_id=rec_id, new_sl=new_potential_sl, metadata={"engine": self.engine_version, "mode": "TRAILING"})
        return None

    def _handle_break_even(self, rec: Dict[str, Any], state: _EngineStateItem) -> Optional[MoveSLAction]:
        """
        Move SL to break-even (entry) when certain conditions met.
        Conditions can be configured in rec dict:
         - break_even_after_profit_pct: Decimal percent
         - break_even_after_ticks: integer (number of ticks since entry)
        """
        rec_id = int(rec["id"])
        entry_raw = rec.get("entry")
        if entry_raw is None:
            return None
        entry = Decimal(str(entry_raw))
        current_sl_raw = rec.get("stop_loss")
        if current_sl_raw is None:
            return None
        current_sl = Decimal(str(current_sl_raw))

        # Condition: reached profit threshold
        pct_cfg = rec.get("break_even_after_profit_pct")
        if pct_cfg is not None:
            target_price = None
            side = str(rec.get("side", "LONG")).upper()
            pct = Decimal(str(pct_cfg))
            if side == "LONG":
                target_price = entry * (Decimal("1") + pct / Decimal("100"))
                if state.highest >= target_price:
                    # candidate: move sl to entry (optionally plus buffer)
                    buffer = Decimal(str(rec.get("break_even_buffer", "0")))
                    new_sl = entry + buffer
                    if new_sl > current_sl:
                        logger.info("Break-even triggered for rec #%d -> new SL %s", rec_id, str(new_sl))
                        return MoveSLAction(rec_id=rec_id, new_sl=new_sl, metadata={"engine": self.engine_version, "mode": "BREAK_EVEN"})
            else:
                target_price = entry * (Decimal("1") - pct / Decimal("100"))
                if state.lowest <= target_price:
                    buffer = Decimal(str(rec.get("break_even_buffer", "0")))
                    new_sl = entry - buffer
                    if new_sl < current_sl:
                        logger.info("Break-even triggered for rec #%d -> new SL %s", rec_id, str(new_sl))
                        return MoveSLAction(rec_id=rec_id, new_sl=new_sl, metadata={"engine": self.engine_version, "mode": "BREAK_EVEN"})
        # Additional time/ticks based conditions can be added similarly
        return None

    def _handle_time_based(self, rec: Dict[str, Any], state: _EngineStateItem, ts: int) -> Optional[Action]:
        """
        Example: close position after N seconds if below certain price conditions.
        Configurable in rec dict via 'time_based_close_after_seconds' and 'time_based_close_threshold'.
        """
        rec_id = int(rec["id"])
        seconds = rec.get("time_based_close_after_seconds")
        if not seconds:
            return None
        threshold = rec.get("time_based_close_threshold")
        # simplistic implementation: if time since init > seconds and last tick shows adverse condition, close
        elapsed = ts - state.initialized_at
        if elapsed < int(seconds):
            return None
        # simple threshold logic: if provided and crossed, close
        if threshold is not None:
            side = str(rec.get("side", "LONG")).upper()
            threshold_price = Decimal(str(threshold))
            # use last observed highest/lowest as proxy
            if side == "LONG" and state.lowest <= threshold_price:
                return CloseAction(rec_id=rec_id, price=threshold_price, reason="TIME_BASED_CLOSE", metadata={"engine": self.engine_version})
            if side == "SHORT" and state.highest >= threshold_price:
                return CloseAction(rec_id=rec_id, price=threshold_price, reason="TIME_BASED_CLOSE", metadata={"engine": self.engine_version})
        return None

    # --- Utility/Debug ---
    def get_state_snapshot(self) -> Dict[str, Any]:
        return self.serialize_state()

    def shutdown(self) -> None:
        """Clean shutdown tasks (no async operations here)."""
        logger.info("StrategyEngine shutdown called")
        # optionally flush to storage synchronously if storage supports it
        try:
            if self.storage and hasattr(self.storage, "set"):
                key = self.config.get("persistence_key", "strategy_engine_state_v4")
                self.storage.set(key, json.dumps(self.serialize_state()))
                logger.debug("Persisted state on shutdown to key=%s", key)
        except Exception:
            logger.exception("Failed to persist state on shutdown")

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/strategy/engine.py ---