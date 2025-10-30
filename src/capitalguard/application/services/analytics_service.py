# --- START OF FINAL, COMPLETE, AND ARCHITECTURALLY-CORRECT FILE (Version 11.1.1 - Decimal Fix) ---
# src/capitalguard/application/services/analytics_service.py

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Union
from math import isfinite
from decimal import Decimal, InvalidOperation # ✅ NEW: Import Decimal and InvalidOperation

from sqlalchemy.orm import Session
from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.application.services.trade_service import _pct, _to_decimal # ✅ THE FIX: Import _pct and _to_decimal from TradeService

@dataclass
class AnalyticsService:
    """
    [cite_start]Provides advanced, user-scoped analytics. [cite: 65]
    All methods now accept a `Session` object, adhering to the Unit of Work pattern,
    [cite_start]ensuring consistent transaction management across the application. [cite: 66]
    """
    repo: RecommendationRepository

    # --- Private Helper Methods ---
    
    @staticmethod
    def _to_int_user_id(user_id: Union[int, str]) -> int:
        """Coerce Telegram user_id to int safely."""
        return int(str(user_id).strip())

    @staticmethod
    def _val(x: Any, attr: str, default: Any = None) -> Any:
        """Safely get .attr if exists, else x itself (for domain ValueObjects)."""
        [cite_start]if x is None: return default [cite: 67]
        return getattr(x, attr, x)

    # ❌ REMOVED: The old, unsafe _pnl_percent is removed. We use trade_service._pct

    # --- Public Service Methods ---

    def win_rate_for_user(self, session: Session, user_id: Union[int, str]) -> float:
        """Calculates the win-rate percentage for a user's closed trades."""
        uid = self._to_int_user_id(user_id)
        items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
 
        [cite_start]if not closed: [cite: 69]
            return 0.0

        wins = sum(1 for r in closed if _pct( # ✅ THE FIX: Use the universal, Decimal-safe _pct
            self._val(r.entry, "value", 0),
            float(r.exit_price or 0),
            self._val(r.side, "value")
        ) > 0)
        
        # NOTE: _pct returns float, so comparison is safe.
        [cite_start]return wins * 100.0 / len(closed) [cite: 70]

    def pnl_curve_for_user(self, session: Session, user_id: Union[int, str]) -> List[Tuple[str, float]]:
        """Generates the cumulative PnL% curve over time for a specific user."""
        uid = self._to_int_user_id(user_id)
        items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None and r.closed_at]
        [cite_start]closed.sort(key=lambda r: r.closed_at) [cite: 71]

        curve, cumulative_pnl = [], 0.0
        for r in closed:
            pnl = _pct( # ✅ THE FIX: Use the universal, Decimal-safe _pct
                _to_decimal(self._val(r.entry, "value", 0)),
                _to_decimal(r.exit_price or 0),
                self._val(r.side, "value")
            )
            cumulative_pnl += pnl
            day = r.closed_at.strftime("%Y-%m-%d")
            curve.append((day, cumulative_pnl))
        return curve

    # [cite_start]✅ FIX: The method now accepts a 'session' argument and no longer manages its own. [cite: 73]
    def performance_summary_for_user(self, session: Session, user_id: Union[int, str]) -> Dict[str, Any]:
        """
        [cite_start]Provides a comprehensive performance summary for a specific user using the provided session. [cite: 73]
        """
        uid = self._to_int_user_id(user_id)
        all_items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        [cite_start]closed_items = [r for r in all_items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None] [cite: 74]
        [cite_start]open_items = [r for r in all_items if r.status != RecommendationStatus.CLOSED] [cite: 74]

        # ✅ THE FIX: Use the universal, Decimal-safe _pct (which returns float)
        total_pnl = sum(
            _pct(
                _to_decimal(self._val(r.entry, "value", 0)),
                _to_decimal(r.exit_price or 0),
                self._val(r.side, "value")
            )
            [cite_start]for r in closed_items [cite: 75]
        )
        
        win_rate = 0.0
        if closed_items:
            wins = sum(1 for r in closed_items if _pct( # ✅ THE FIX: Use the universal, Decimal-safe _pct
                _to_decimal(self._val(r.entry, "value", 0)), 
                _to_decimal(r.exit_price or 0), 
                self._val(r.side, "value")
            ) > 0)
            [cite_start]win_rate = wins * 100.0 / len(closed_items) [cite: 76]

        return {
            "total_recommendations": len(all_items),
            "open_recommendations": len(open_items),
            "closed_recommendations": len(closed_items),
            "overall_win_rate": f"{win_rate:.2f}%",
            [cite_start]"total_pnl_percent": f"{total_pnl:.2f}%", [cite: 77]
        }

# --- END OF FINAL, COMPLETE, AND ARCHITECTURALLY-CORRECT FILE (Version 11.1.1 - Decimal Fix) ---