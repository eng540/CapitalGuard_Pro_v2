--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/strategy/engine.py ---
"""
StrategyEngine v2.2 - Final Logic Implementation with optional LifecycleService integration.

- Accepts an optional LifecycleService via constructor for tighter integration with system state.
- Remains decoupled from DB by default; uses lifecycle_service only when it exposes safe, public methods.
- Returns Action objects for the AlertService to execute.
- Implements Fixed and Trailing stops logic (stateful).
- Robust: defensive checks around lifecycle_service to avoid runtime errors when methods are absent.
"""

import logging
from decimal import Decimal
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from capitalguard.infrastructure.db.models import RecommendationStatusEnum
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.lifecycle_service import LifecycleService

logger = logging.getLogger(__name__)

# --- Action Data Classes ---
@dataclass
class BaseAction:
    rec_id: int

@dataclass
class CloseAction(BaseAction):
    price: Decimal
    reason: str

@dataclass
class MoveSLAction(BaseAction):
    new_sl: Decimal

# --- Strategy Engine ---

class StrategyEngine:
    """
    Evaluates exit strategies using a stateful, in-memory cache.
    Optionally integrates with LifecycleService for state synchronization and side-effects.
    """

    def __init__(self, trade_service: TradeService, lifecycle_service: Optional[LifecycleService] = None):
        """
        :param trade_service: required service for trade related helpers (read-only usage).
        :param lifecycle_service: optional service to synchronize state, persist changes, or invoke lifecycle hooks.
                                  If provided, StrategyEngine will call safe methods on it when available.
        """
        self.trade_service = trade_service
        self.lifecycle_service = lifecycle_service
        # In-memory state cache: {rec_id: {"highest": Decimal, "lowest": Decimal, "in_profit_zone": bool}}
        self._state: Dict[int, Dict[str, Any]] = {}

    # -------------------------
    # Public state management
    # -------------------------
    def initialize_state_for_recommendation(self, rec_dict: Dict[str, Any]):
        """
        Initialize or refresh in-memory state for a recommendation dict.
        If lifecycle_service exposes a way to fetch canonical values, prefer it (defensive).
        """
        rec_id = int(rec_dict['id'])
        try:
            entry = Decimal(str(rec_dict.get('entry', '0')))
        except Exception:
            entry = Decimal('0')

        status = rec_dict.get('status')
        # Ensure only active recommendations keep state
        if status == RecommendationStatusEnum.ACTIVE:
            self._state[rec_id] = {
                "highest": entry,
                "lowest": entry,
                "in_profit_zone": False,
            }
            logger.debug(f"Initialized state for rec #{rec_id}: entry={entry}")
            # If lifecycle_service provides a hook, call it (non-fatal)
            try:
                if self.lifecycle_service and hasattr(self.lifecycle_service, "on_engine_state_init"):
                    # Safe call, lifecycle_service may accept rec_id or rec_dict
                    try:
                        self.lifecycle_service.on_engine_state_init(rec_id, rec_dict)
                    except TypeError:
                        # fallback signature
                        self.lifecycle_service.on_engine_state_init(rec_id)
            except Exception as e:
                logger.debug(f"Non-fatal lifecycle_service.on_engine_state_init failed for #{rec_id}: {e}")
        else:
            # remove stale/closed entries
            self._state.pop(rec_id, None)
            logger.debug(f"Recommendation #{rec_id} not active; state cleared on init call.")

    def clear_state(self, rec_id: int):
        """
        Clear in-memory state for a recommendation and notify lifecycle_service if available.
        """
        self._state.pop(rec_id, None)
        logger.debug(f"Cleared in-memory state for rec #{rec_id}")
        # Notify lifecycle_service if it supports it (best-effort)
        try:
            if self.lifecycle_service and hasattr(self.lifecycle_service, "on_engine_state_cleared"):
                try:
                    self.lifecycle_service.on_engine_state_cleared(rec_id)
                except TypeError:
                    self.lifecycle_service.on_engine_state_cleared(rec_id, None)
        except Exception as e:
            logger.debug(f"Non-fatal lifecycle_service.on_engine_state_cleared failed for #{rec_id}: {e}")

    # -------------------------
    # Core evaluation API
    # -------------------------
    def evaluate(self, rec_dict: Dict[str, Any], high_price: Decimal, low_price: Decimal) -> List[BaseAction]:
        """
        Evaluate a single recommendation dict and return actions to take.
        This method is safe to call on each price tick.

        :param rec_dict: recommendation state snapshot (should include id, status, side, stop_loss and profit stop fields)
        :param high_price: Decimal high price observed during the tick
        :param low_price: Decimal low price observed during the tick
        :return: list of BaseAction instances (CloseAction / MoveSLAction)
        """
        actions: List[BaseAction] = []

        if not rec_dict:
            return actions

        try:
            rec_status = rec_dict.get('status')
        except Exception:
            rec_status = None

        rec_id = int(rec_dict.get('id'))

        # If lifecycle_service provides a canonical refresh method, attempt to refresh rec_dict (best-effort)
        try:
            if self.lifecycle_service and hasattr(self.lifecycle_service, "get_latest_recommendation"):
                try:
                    refreshed = self.lifecycle_service.get_latest_recommendation(rec_id)
                    if refreshed:
                        # Assume refreshed is dict-like
                        rec_dict = refreshed
                except Exception as e:
                    logger.debug(f"lifecycle_service.get_latest_recommendation failed for #{rec_id}: {e}")
        except Exception:
            pass

        if rec_status != RecommendationStatusEnum.ACTIVE:
            # ensure state is cleaned up for non-active recs
            self.clear_state(rec_id)
            return actions

        # If profit stop is not active or not configured, skip
        if not rec_dict.get('profit_stop_active'):
            return actions

        # Ensure state exists
        if rec_id not in self._state:
            self.initialize_state_for_recommendation(rec_dict)

        state = self._state.get(rec_id)
        if not state:
            # defensive: if state still missing, skip
            logger.debug(f"No state available for rec #{rec_id}; skipping.")
            return actions

        side = (rec_dict.get('side') or 'LONG').upper()
        mode = (rec_dict.get('profit_stop_mode') or 'NONE').upper()

        # Update highest/lowest trackers
        try:
            if side == "LONG":
                if high_price > state["highest"]:
                    state["highest"] = high_price
            else:  # SHORT
                if low_price < state["lowest"]:
                    state["lowest"] = low_price
        except Exception as e:
            logger.debug(f"Error updating high/low for rec #{rec_id}: {e}")

        # Execute strategy logic
        try:
            if mode == "FIXED":
                action = self._handle_fixed_profit_stop(rec_dict, high_price, low_price, state)
                if action:
                    actions.append(action)
            elif mode == "TRAILING":
                action = self._handle_trailing_stop(rec_dict, state)
                if action:
                    actions.append(action)
        except Exception as e:
            logger.exception(f"Error evaluating strategy for rec #{rec_id}: {e}")

        # If any actions produced, optionally notify lifecycle_service so it can persist or react.
        if actions and self.lifecycle_service:
            try:
                if hasattr(self.lifecycle_service, "on_engine_actions"):
                    # Expected signature: on_engine_actions(rec_id, actions_list)
                    try:
                        self.lifecycle_service.on_engine_actions(rec_id, actions)
                    except TypeError:
                        # fallback single-action signature
                        for a in actions:
                            try:
                                self.lifecycle_service.on_engine_action(a)
                            except Exception:
                                # best-effort
                                pass
                elif hasattr(self.lifecycle_service, "enqueue_engine_actions"):
                    try:
                        self.lifecycle_service.enqueue_engine_actions(rec_id, actions)
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Non-fatal lifecycle_service action notification failed for #{rec_id}: {e}")

        return actions

    # -------------------------
    # Strategy implementations
    # -------------------------
    def _handle_fixed_profit_stop(self, rec_dict: Dict[str, Any], high_price: Decimal, low_price: Decimal, state: Dict) -> Optional[CloseAction]:
        """
        Fixed profit stop (take-profit-stop) logic:
        - Mark entry into profit zone when price reaches profit_price.
        - Trigger close when price retraces back to profit_price (i.e., confirms pullback).
        """
        try:
            profit_price_raw = rec_dict.get('profit_stop_price')
            if profit_price_raw is None:
                return None
            profit_price = Decimal(str(profit_price_raw))
        except Exception:
            return None

        side = (rec_dict.get('side') or 'LONG').upper()
        rec_id = int(rec_dict['id'])

        # Step 1: Detect entry into profit zone
        if not state.get("in_profit_zone", False):
            entered = (side == "LONG" and high_price >= profit_price) or (side == "SHORT" and low_price <= profit_price)
            if entered:
                state["in_profit_zone"] = True
                logger.info(f"Rec #{rec_id} entered profit zone (FIXED) at {profit_price:g}")

        # Step 2: If in profit zone, detect retrace that hits the profit_price -> close
        if state.get("in_profit_zone", False):
            retraced = (side == "LONG" and low_price <= profit_price) or (side == "SHORT" and high_price >= profit_price)
            if retraced:
                logger.info(f"FIXED profit stop triggered for Rec #{rec_id} at price {profit_price:g}")
                # Optionally clear state immediately (engine-level)
                try:
                    self.clear_state(rec_id)
                except Exception:
                    pass
                return CloseAction(rec_id=rec_id, price=profit_price, reason="PROFIT_STOP_HIT")
        return None

    def _handle_trailing_stop(self, rec_dict: Dict[str, Any], state: Dict) -> Optional[MoveSLAction]:
        """
        Trailing stop logic:
        - Use highest/lowest observed reference to compute potential new SL.
        - Support both percentage-based and absolute trailing values (heuristic: <=10 -> percent).
        - Only propose MoveSLAction when the new SL is strictly 'better' (protects more profit).
        """
        rec_id = int(rec_dict['id'])
        try:
            current_sl = Decimal(str(rec_dict.get('stop_loss')))
        except Exception:
            # If stop_loss is invalid, do nothing
            logger.debug(f"Rec #{rec_id} has invalid stop_loss; skipping trailing logic.")
            return None

        trailing_raw = rec_dict.get('profit_stop_trailing_value')
        if trailing_raw is None:
            return None

        try:
            trailing_value = Decimal(str(trailing_raw))
        except Exception:
            logger.debug(f"Rec #{rec_id} trailing value invalid: {trailing_raw}")
            return None

        side = (rec_dict.get('side') or 'LONG').upper()

        # Decide if trailing_value is percentage (heuristic)
        is_percentage = trailing_value <= Decimal('10')

        new_potential_sl = None
        try:
            if side == "LONG":
                reference_price = Decimal(state.get("highest", 0))
                if is_percentage:
                    distance = (reference_price * (trailing_value / Decimal('100')))
                else:
                    distance = trailing_value
                new_potential_sl = reference_price - distance
            else:  # SHORT
                reference_price = Decimal(state.get("lowest", 0))
                if is_percentage:
                    distance = (reference_price * (trailing_value / Decimal('100')))
                else:
                    distance = trailing_value
                new_potential_sl = reference_price + distance
        except Exception as e:
            logger.debug(f"Error computing new potential SL for rec #{rec_id}: {e}")
            return None

        # Compare if the new potential SL is strictly better
        is_better = False
        try:
            if side == "LONG" and new_potential_sl > current_sl:
                is_better = True
            elif side == "SHORT" and new_potential_sl < current_sl:
                is_better = True
        except Exception:
            is_better = False

        if is_better:
            logger.info(f"TRAILING: propose MoveSL for Rec #{rec_id}: {current_sl:g} -> {new_potential_sl:g}")
            # Optionally notify lifecycle_service immediately about proposed SL update (best-effort)
            try:
                if self.lifecycle_service and hasattr(self.lifecycle_service, "on_engine_trailing_update"):
                    try:
                        self.lifecycle_service.on_engine_trailing_update(rec_id, current_sl, new_potential_sl)
                    except TypeError:
                        # fallback
                        self.lifecycle_service.on_engine_trailing_update(rec_id, new_potential_sl)
            except Exception as e:
                logger.debug(f"Non-fatal lifecycle trailing hook failed for #{rec_id}: {e}")

            return MoveSLAction(rec_id=rec_id, new_sl=new_potential_sl)

        return None
--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/strategy/engine.py ---