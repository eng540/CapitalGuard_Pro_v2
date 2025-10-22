# src/capitalguard/application/strategy/engine.py (NEW FILE - v1.0)
"""
StrategyEngine v1.0 - Stateful, reliable, and focused rule engine for exit strategies.
"""
import logging
from decimal import Decimal
from typing import Dict

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import Recommendation, RecommendationEvent, RecommendationStatusEnum
from capitalguard.application.services.trade_service import TradeService

logger = logging.getLogger(__name__)

class StrategyEngine:
    """
    Evaluates advanced exit strategies (Fixed Profit Stop, Trailing Stop)
    using a stateful in-memory cache for high performance and accuracy.
    """
    def __init__(self, trade_service: TradeService):
        self.trade_service = trade_service
        self._state: Dict[int, Dict] = {}  # In-memory state cache: {rec_id: {"highest": Decimal, "lowest": Decimal, "in_profit_zone": bool}}

    def initialize_state_for_recommendation(self, rec: Recommendation):
        """Initializes or resets the in-memory state for a recommendation."""
        if rec.status == RecommendationStatusEnum.ACTIVE:
            self._state[rec.id] = {
                "highest": Decimal(str(rec.entry)),
                "lowest": Decimal(str(rec.entry)),
                "in_profit_zone": False,
            }
        else:
            self._state.pop(rec.id, None)

    async def evaluate_recommendation(self, session: Session, rec: Recommendation, high_price: Decimal, low_price: Decimal):
        """
        Evaluates a single recommendation against a price tick.
        This is the core logic method, designed to be called by AlertService.
        """
        if not rec or rec.status != RecommendationStatusEnum.ACTIVE or not rec.profit_stop_active:
            self._state.pop(rec.id, None) # Clean up state for non-active recs
            return

        rec_id = rec.id
        if rec_id not in self._state:
            self.initialize_state_for_recommendation(rec)
        
        state = self._state[rec_id]
        side = rec.side.upper()
        mode = rec.profit_stop_mode.upper()

        # Update highest/lowest price tracking
        if side == "LONG":
            if high_price > state["highest"]: state["highest"] = high_price
        else: # SHORT
            if low_price < state["lowest"]: state["lowest"] = low_price

        # --- Execute Strategy Logic ---
        if mode == "FIXED":
            await self._handle_fixed_profit_stop(session, rec, high_price, low_price, state)
        elif mode == "TRAILING":
            await self._handle_trailing_stop(session, rec, state)

    async def _handle_fixed_profit_stop(self, session: Session, rec: Recommendation, high_price: Decimal, low_price: Decimal, state: Dict):
        """Correctly handles the logic for a fixed profit stop."""
        profit_price = Decimal(str(rec.profit_stop_price))
        side = rec.side.upper()

        # Step 1: Check if we have entered the profit zone
        if not state["in_profit_zone"]:
            if (side == "LONG" and high_price >= profit_price) or \
               (side == "SHORT" and low_price <= profit_price):
                state["in_profit_zone"] = True
                logger.info(f"Rec #{rec.id} entered profit zone for fixed stop at {profit_price:g}.")
        
        # Step 2: If in the zone, check if price has retraced to the stop level
        if state["in_profit_zone"]:
            if (side == "LONG" and low_price <= profit_price) or \
               (side == "SHORT" and high_price >= profit_price):
                logger.info(f"FIXED profit stop triggered for Rec #{rec.id} at price {profit_price:g}")
                analyst_uid = str(rec.analyst.telegram_user_id) if rec.analyst else None
                await self.trade_service.close_recommendation_async(rec.id, analyst_uid, profit_price, session, reason="PROFIT_STOP_HIT")
                self._state.pop(rec.id, None) # Clean up state after closing

    async def _handle_trailing_stop(self, session: Session, rec: Recommendation, state: Dict):
        """Correctly handles the logic for a trailing stop loss."""
        current_sl = Decimal(str(rec.stop_loss))
        trailing_value = Decimal(str(rec.profit_stop_trailing_value))
        side = rec.side.upper()
        
        # Determine if trailing value is percentage or absolute distance
        is_percentage = trailing_value <= 10 # Heuristic: values > 10 are likely price distances
        
        new_potential_sl = None
        if side == "LONG":
            reference_price = state["highest"]
            distance = reference_price * (trailing_value / 100) if is_percentage else trailing_value
            new_potential_sl = reference_price - distance
        else: # SHORT
            reference_price = state["lowest"]
            distance = reference_price * (trailing_value / 100) if is_percentage else trailing_value
            new_potential_sl = reference_price + distance

        # Update SL only if the new value is an improvement
        is_better = (side == "LONG" and new_potential_sl > current_sl) or \
                    (side == "SHORT" and new_potential_sl < current_sl)

        if is_better:
            logger.info(f"TRAILING stop update for Rec #{rec.id}. Moving SL from {current_sl:g} to {new_potential_sl:g}")
            analyst_uid = str(rec.analyst.telegram_user_id) if rec.analyst else None
            await self.trade_service.update_sl_for_user_async(rec.id, analyst_uid, new_potential_sl, session)