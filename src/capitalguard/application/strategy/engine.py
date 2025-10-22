# src/capitalguard/application/strategy/engine.py (v1.1 - Dict Hotfix)
"""
StrategyEngine v1.1 - Stateful, reliable, and focused rule engine for exit strategies.
✅ HOTFIX: Refactored to work with dictionaries instead of ORM objects to prevent
AttributeError and align with AlertService's data structure.
"""

import logging
from decimal import Decimal
from typing import Dict, Any

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import RecommendationEvent, RecommendationStatusEnum
from capitalguard.application.services.trade_service import TradeService

logger = logging.getLogger(__name__)

class StrategyEngine:
    """
    Evaluates advanced exit strategies using a stateful in-memory cache.
    This version operates on dictionaries to remain decoupled from SQLAlchemy sessions.
    """
    def __init__(self, trade_service: TradeService):
        self.trade_service = trade_service
        self._state: Dict[int, Dict] = {}

    def initialize_state_for_recommendation(self, rec_data: Dict[str, Any]):
        """Initializes or resets the in-memory state for a recommendation from a dictionary."""
        rec_id = rec_data['id']
        if rec_data['status'] == RecommendationStatusEnum.ACTIVE:
            self._state[rec_id] = {
                "highest": rec_data['entry'],
                "lowest": rec_data['entry'],
                "in_profit_zone": False,
            }
        else:
            self._state.pop(rec_id, None)

    async def evaluate_recommendation(self, trigger_data: Dict[str, Any], high_price: Decimal, low_price: Decimal):
        """
        Evaluates a single recommendation (as a dict) against a price tick.
        This is the core logic method, designed to be called by AlertService.
        """
        if not trigger_data or trigger_data['status'] != RecommendationStatusEnum.ACTIVE or not trigger_data.get('profit_stop_active'):
            self._state.pop(trigger_data.get('id'), None)
            return

        rec_id = trigger_data['id']
        if rec_id not in self._state:
            self.initialize_state_for_recommendation(trigger_data)
        
        state = self._state[rec_id]
        side = trigger_data['side'].upper()
        mode = (trigger_data.get('profit_stop_mode') or 'NONE').upper()

        # Update highest/lowest price tracking
        if side == "LONG":
            if high_price > state["highest"]: state["highest"] = high_price
        else: # SHORT
            if low_price < state["lowest"]: state["lowest"] = low_price

        # --- Execute Strategy Logic ---
        if mode == "FIXED":
            await self._handle_fixed_profit_stop(trigger_data, high_price, low_price, state)
        elif mode == "TRAILING":
            await self._handle_trailing_stop(trigger_data, state)

    async def _handle_fixed_profit_stop(self, rec_data: Dict[str, Any], high_price: Decimal, low_price: Decimal, state: Dict):
        """Correctly handles the logic for a fixed profit stop."""
        profit_price = rec_data.get('profit_stop_price')
        if not profit_price: return

        side = rec_data['side'].upper()
        rec_id = rec_data['id']

        if not state["in_profit_zone"]:
            if (side == "LONG" and high_price >= profit_price) or \
               (side == "SHORT" and low_price <= profit_price):
                state["in_profit_zone"] = True
                logger.info(f"Rec #{rec_id} entered profit zone for fixed stop at {profit_price:g}.")
        
        if state["in_profit_zone"]:
            if (side == "LONG" and low_price <= profit_price) or \
               (side == "SHORT" and high_price >= profit_price):
                logger.info(f"FIXED profit stop triggered for Rec #{rec_id} at price {profit_price:g}")
                # Use create_task to avoid blocking the alert loop
                asyncio.create_task(
                    self.trade_service.close_recommendation_async(rec_id, rec_data['user_id'], profit_price, reason="PROFIT_STOP_HIT")
                )
                self._state.pop(rec_id, None)

    async def _handle_trailing_stop(self, rec_data: Dict[str, Any], state: Dict):
        """Correctly handles the logic for a trailing stop loss."""
        trailing_value = rec_data.get('profit_stop_trailing_value')
        if not trailing_value: return

        current_sl = rec_data['stop_loss']
        side = rec_data['side'].upper()
        rec_id = rec_data['id']
        
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
            logger.info(f"TRAILING stop update for Rec #{rec_id}. Moving SL from {current_sl:g} to {new_potential_sl:g}")
            asyncio.create_task(
                self.trade_service.update_sl_for_user_async(rec_id, rec_data['user_id'], new_potential_sl)
            )