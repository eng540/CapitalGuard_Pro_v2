# --- START OF FINAL, FULLY CORRECTED AND ROBUST FILE: src/capitalguard/application/services/analytics_service.py ---
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any, Union
from datetime import datetime
from math import isfinite

from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import RecommendationRepository

@dataclass
class AnalyticsService:
    """Provides advanced, user-scoped analytics."""
    repo: RecommendationRepository

    # ---------------------------
    # Helpers
    # ---------------------------
    @staticmethod
    def _to_int_user_id(user_id: Union[int, str]) -> int:
        """Coerce Telegram user_id to int safely."""
        return int(str(user_id).strip())

    @staticmethod
    def _val(x: Any, attr: str, default: Any = None) -> Any:
        """Safely get .attr if exists, else x itself (for domain ValueObjects)."""
        if x is None:
            return default
        return getattr(x, attr, x)

    @staticmethod
    def _pnl_percent(side: str, entry: float, exit_price: float) -> float:
        """
        PnL % based on direction:
          LONG:  (exit/entry - 1) * 100
          SHORT: (entry/exit - 1) * 100
        """
        s = (side or "").upper()
        if not entry or not exit_price or entry == 0:
            return 0.0
        if s == "LONG":
            return (exit_price / entry - 1.0) * 100.0
        return (entry / exit_price - 1.0) * 100.0

    @staticmethod
    def _rr(entry: float, sl: float, tp1: Optional[float], side: str) -> Optional[float]:
        """Theoretical R:R using first target."""
        try:
            if tp1 is None:
                return None
            risk = abs(entry - sl)
            reward = abs(tp1 - entry) if side.upper() == "LONG" else abs(entry - tp1)
            if risk <= 0 or reward <= 0:
                return None
            r = reward / risk
            return r if isfinite(r) else None
        except Exception:
            return None

    # ---------------------------
    # User-scoped metrics
    # ---------------------------
    def win_rate_for_user(self, user_id: Union[int, str]) -> float:
        """Win-rate % for a user's closed trades with an exit price."""
        uid = self._to_int_user_id(user_id)
        with SessionLocal() as session:
            items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        if not closed:
            return 0.0

        wins = 0
        for r in closed:
            side = self._val(r.side, "value", r.side)
            entry = float(self._val(r.entry, "value", r.entry) or 0)
            exit_price = float(r.exit_price or 0)
            pnl = self._pnl_percent(side, entry, exit_price)
            if pnl > 0:
                wins += 1
        return wins * 100.0 / len(closed)

    def pnl_curve_for_user(self, user_id: Union[int, str]) -> List[Tuple[str, float]]:
        """
        Cumulative PnL% over time (by closed_at day) for a specific user.
        Returns list of (YYYY-MM-DD, cumulative_pnl_percent).
        """
        uid = self._to_int_user_id(user_id)
        with SessionLocal() as session:
            items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None and r.closed_at]
        closed.sort(key=lambda r: r.closed_at)

        curve, cumulative = [], 0.0
        for r in closed:
            side = self._val(r.side, "value", r.side)
            entry = float(self._val(r.entry, "value", r.entry) or 0)
            exit_price = float(r.exit_price or 0)
            pnl = self._pnl_percent(side, entry, exit_price)
            cumulative += pnl
            day = r.closed_at.strftime("%Y-%m-%d")
            curve.append((day, cumulative))
        return curve

    def summary_by_market_for_user(self, user_id: Union[int, str]) -> Dict[str, Dict[str, float]]:
        """
        Grouped stats by market for a specific user:
          { market: { count, win_rate, sum_pnl } }
        """
        uid = self._to_int_user_id(user_id)
        with SessionLocal() as session:
            items = self.repo.list_all_for_user(session, user_telegram_id=uid)

        buckets: Dict[str, List] = {}
        for r in items:
            m = str(self._val(self._val(r, "market"), "value", getattr(r, "market", "Unknown")))
            buckets.setdefault(m, []).append(r)

        out: Dict[str, Dict[str, float]] = {}
        for m, arr in buckets.items():
            closed = [x for x in arr if x.status == RecommendationStatus.CLOSED and x.exit_price is not None]
            wr = 0.0
            if closed:
                wins = 0
                for x in closed:
                    side = self._val(x.side, "value", x.side)
                    entry = float(self._val(x.entry, "value", x.entry) or 0)
                    exit_price = float(x.exit_price or 0)
                    if self._pnl_percent(side, entry, exit_price) > 0:
                        wins += 1
                wr = wins * 100.0 / len(closed)

            sum_pnl = sum(
                self._pnl_percent(
                    self._val(x.side, "value", x.side),
                    float(self._val(x.entry, "value", x.entry) or 0),
                    float(getattr(x, "exit_price", 0) or 0),
                )
                for x in closed
            )
            out[m] = {"count": float(len(arr)), "win_rate": wr, "sum_pnl": sum_pnl}
        return out

    def performance_summary_for_user(self, user_id: Union[int, str]) -> Dict[str, Any]:
        """
        Comprehensive performance summary for a specific user.
        """
        uid = self._to_int_user_id(user_id)
        with SessionLocal() as session:
            all_items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        
        closed_items = [r for r in all_items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        open_items = [r for r in all_items if r.status != RecommendationStatus.CLOSED]

        total_pnl = sum(
            self._pnl_percent(
                self._val(r.side, "value", r.side),
                float(self._val(r.entry, "value", r.entry) or 0),
                float(r.exit_price or 0),
            )
            for r in closed_items
        )

        return {
            "total_recommendations": len(all_items),
            "open_recommendations": len(open_items),
            "closed_recommendations": len(closed_items),
            "overall_win_rate": f"{self.win_rate_for_user(uid):.2f}%",
            "total_pnl_percent": f"{total_pnl:.2f}%",
        }

    # Note: The global performance_summary is deprecated and has been removed
    # as it does not fit the user-scoped session management pattern.
# --- END OF FINAL, FULLY CORRECTED AND ROBUST FILE ---