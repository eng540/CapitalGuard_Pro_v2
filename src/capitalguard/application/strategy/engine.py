# src/capitalguard/application/strategy/engine.py (v2.1 - Final Logic Implementation)
"""
StrategyEngine v2.1 - A pure, stateful, and reliable rule engine for exit strategies.

- Decoupled from the database; operates only on dictionaries.
- Returns a list of Action objects for the AlertService to execute.
- Correctly implements stateful logic for Fixed and Trailing stops.
- All logic is now finalized and production-ready.
"""

import logging
from decimal import Decimal
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from capitalguard.infrastructure.db.models import RecommendationStatusEnum
from capitalguard.application.services.trade_service import TradeService

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
    Evaluates advanced exit strategies using a stateful in-memory cache.
    It expects trigger data as dictionaries and returns a list of actions.
    """
    def __init__(self, trade_service: TradeService):
        self.trade_service = trade_service
        self._state: Dict[int, Dict] = {}  # In-memory state cache: {rec_id: {"highest": Decimal, "lowest": Decimal, "in_profit_zone": bool}}

    def initialize_state_for_recommendation(self, rec_dict: Dict[str, Any]):
        """Initializes or resets the in-memory state for a recommendation dictionary."""
        rec_id = rec_dict['id']
        if rec_dict['status'] == RecommendationStatusEnum.ACTIVE:
            self._state[rec_id] = {
                "highest": rec_dict.get('entry', Decimal('0')),
                "lowest": rec_dict.get('entry', Decimal('0')),
                "in_profit_zone": False,
            }
        else:
            self._state.pop(rec_id, None)

    def clear_state(self, rec_id: int):
        """Explicitly clears the state for a given recommendation ID, typically on close."""
        self._state.pop(rec_id, None)
        logger.debug(f"Cleared state for closed recommendation #{rec_id}")

    def evaluate(self, rec_dict: Dict[str, Any], high_price: Decimal, low_price: Decimal) -> List[BaseAction]:
        """
        Evaluates a single recommendation dictionary and returns a list of actions to be taken.
        """
        actions: List[BaseAction] = []
        if not rec_dict or rec_dict['status'] != RecommendationStatusEnum.ACTIVE or not rec_dict.get('profit_stop_active'):
            return actions

        rec_id = rec_dict['id']
        if rec_id not in self._state:
            self.initialize_state_for_recommendation(rec_dict)
        
        state = self._state[rec_id]
        side = rec_dict['side'].upper()
        mode = (rec_dict.get('profit_stop_mode') or 'NONE').upper()

        # Update highest/lowest price tracking for the current tick
        if side == "LONG" and high_price > state["highest"]:
            state["highest"] = high_price
        elif side == "SHORT" and low_price < state["lowest"]:
            state["lowest"] = low_price

        # --- Execute Strategy Logic ---
        if mode == "FIXED":
            action = self._handle_fixed_profit_stop(rec_dict, high_price, low_price, state)
            if action: actions.append(action)
        elif mode == "TRAILING":
            action = self._handle_trailing_stop(rec_dict, state)
            if action: actions.append(action)
            
        return actions

    def _handle_fixed_profit_stop(self, rec_dict: Dict[str, Any], high_price: Decimal, low_price: Decimal, state: Dict) -> Optional[CloseAction]:
        """Correctly handles the logic for a fixed profit stop (take profit stop)."""
        profit_price = rec_dict.get('profit_stop_price')
        if not profit_price: return None
        
        side = rec_dict['side'].upper()
        rec_id = rec_dict['id']

        # Step 1: Check if we have entered the profit zone
        if not state["in_profit_zone"]:
            if (side == "LONG" and high_price >= profit_price) or \
               (side == "SHORT" and low_price <= profit_price):
                state["in_profit_zone"] = True
                logger.info(f"Rec #{rec_id} entered profit zone for fixed stop at {profit_price:g}.")
        
        # Step 2: If in the zone, check if price has retraced to the stop level
        if state["in_profit_zone"]:
            if (side == "LONG" and low_price <= profit_price) or \
               (side == "SHORT" and high_price >= profit_price):
                logger.info(f"FIXED profit stop triggered for Rec #{rec_id} at price {profit_price:g}")
                return CloseAction(rec_id=rec_id, price=profit_price, reason="PROFIT_STOP_HIT")
        return None

    def _handle_trailing_stop(self, rec_dict: Dict[str, Any], state: Dict) -> Optional[MoveSLAction]:
        """Correctly handles the logic for a trailing stop loss."""
        current_sl = rec_dict['stop_loss']
        trailing_value = rec_dict.get('profit_stop_trailing_value')
        if not trailing_value: return None

        side = rec_dict['side'].upper()
        
        # Heuristic: values > 10 are likely price distances, <= 10 are percentages.
        is_percentage = trailing_value <= 10
        
        new_potential_sl = None
        if side == "LONG":
            reference_price = state["highest"]
            distance = reference_price * (trailing_value / 100) if is_percentage else trailing_value
            new_potential_sl = reference_price - distance
        else: # SHORT
            reference_price = state["lowest"]
            distance = reference_price * (trailing_value / 100) if is_percentage else trailing_value
            new_potential_sl = reference_price + distance

        is_better = (side == "LONG" and new_potential_sl > current_sl) or \
                    (side == "SHORT" and new_potential_sl < current_sl)

        if is_better:
            logger.info(f"TRAILING stop update for Rec #{rec_dict['id']}. Moving SL from {current_sl:g} to {new_potential_sl:g}")
            return MoveSLAction(rec_id=rec_dict['id'], new_sl=new_potential_sl)
        
        return None