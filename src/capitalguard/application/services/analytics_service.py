# --- START OF FINAL, COMPLETE, AND ARCHITECTURALLY-CORRECT FILE (Version 11.1.2 - Circular Dep Fix) ---
# src/capitalguard/application/services/analytics_service.py

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Union
from math import isfinite
from decimal import Decimal, InvalidOperation 
import logging # Added for logging warnings

from sqlalchemy.orm import Session
from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.repository import RecommendationRepository
# ❌ REMOVED: from capitalguard.application.services.trade_service import _pct, _to_decimal
# This import caused a circular dependency. Helper functions are now inlined.

log = logging.getLogger(__name__)

# --- Inlined Helper Functions (Copied from trade_service.py) ---
# ✅ THE FIX: Copied helper functions locally to break circular dependency.
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
        log.warning(f"AnalyticsService: Could not convert '{value}' to Decimal.")
        return default

def _pct(entry: Any, target_price: Any, side: str) -> float:
    """Calculates PnL percentage using Decimal, returns float."""
    try:
        entry_dec = _to_decimal(entry)
        target_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite():
            return 0.0
        side_upper = (str(side.value) if hasattr(side, 'value') else str(side) or "").upper()
        if side_upper == "LONG":
            pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT":
            pnl = ((entry_dec / target_dec) - 1) * 100
        else:
            return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError):
        return 0.0
# --- End of Inlined Helpers ---


@dataclass
class AnalyticsService:
    """
    Provides advanced, user-scoped analytics.
    All methods now accept a `Session` object, adhering to the Unit of Work pattern,
    ensuring consistent transaction management across the application.
    """
    repo: RecommendationRepository # ✅ THE FIX: This correctly expects an instance, matching boot.py

    # --- Private Helper Methods ---
    
    @staticmethod
    def _to_int_user_id(user_id: Union[int, str]) -> int:
        """Coerce Telegram user_id to int safely."""
        return int(str(user_id).strip())

    @staticmethod
    def _val(x: Any, attr: str, default: Any = None) -> Any:
        """Safely get .attr if exists, else x itself (for domain ValueObjects)."""
        if x is None: return default
        return getattr(x, attr, x)

    # --- Public Service Methods ---

    def win_rate_for_user(self, session: Session, user_id: Union[int, str]) -> float:
        """Calculates the win-rate percentage for a user's closed trades."""
        uid = self._to_int_user_id(user_id)
        # Use self.repo (the instance) to call the method with the session
        items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
 
        if not closed:
            return 0.0

        # Use inlined _pct and _to_decimal
        wins = sum(1 for r in closed if _pct( 
            _to_decimal(self._val(r.entry, "value", 0)),
            _to_decimal(r.exit_price or 0),
            self._val(r.side, "value")
        ) > 0)
        
        return wins * 100.0 / len(closed)

    def pnl_curve_for_user(self, session: Session, user_id: Union[int, str]) -> List[Tuple[str, float]]:
        """Generates the cumulative PnL% curve over time for a specific user."""
        uid = self._to_int_user_id(user_id)
        items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None and r.closed_at]
        closed.sort(key=lambda r: r.closed_at)

        curve, cumulative_pnl = [], 0.0
        for r in closed:
            # Use inlined _pct and _to_decimal
            pnl = _pct( 
                _to_decimal(self._val(r.entry, "value", 0)),
                _to_decimal(r.exit_price or 0),
                self._val(r.side, "value")
            )
            cumulative_pnl += pnl
            day = r.closed_at.strftime("%Y-%m-%d")
            curve.append((day, cumulative_pnl))
        return curve

    def performance_summary_for_user(self, session: Session, user_id: Union[int, str]) -> Dict[str, Any]:
        """
        Provides a comprehensive performance summary for a specific user using the provided session.
        """
        uid = self._to_int_user_id(user_id)
        all_items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        closed_items = [r for r in all_items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        open_items = [r for r in all_items if r.status != RecommendationStatus.CLOSED]

        # Use inlined _pct and _to_decimal
        total_pnl = sum(
            _pct(
                _to_decimal(self._val(r.entry, "value", 0)),
                _to_decimal(r.exit_price or 0),
                self._val(r.side, "value")
            )
            for r in closed_items
        )
        
        win_rate = 0.0
        if closed_items:
            wins = sum(1 for r in closed_items if _pct( 
                _to_decimal(self._val(r.entry, "value", 0)), 
                _to_decimal(r.exit_price or 0), 
                self._val(r.side, "value")
            ) > 0)
            win_rate = wins * 100.0 / len(closed_items)

        return {
            "total_recommendations": len(all_items),
            "open_recommendations": len(open_items),
            "closed_recommendations": len(closed_items),
            "overall_win_rate": f"{win_rate:.2f}%",
            "total_pnl_percent": f"{total_pnl:.2f}%",
        }

# --- END OF FINAL, COMPLETE, AND ARCHITECTURALLY-CORRECT FILE (Version 11.1.2 - Circular Dep Fix) ---