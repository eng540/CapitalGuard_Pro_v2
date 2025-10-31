# src/capitalguard/application/services/analytics_service.py
# Version 11.1.2 — Circular Dependency Fix

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Union
from decimal import Decimal, InvalidOperation
import logging
from sqlalchemy.orm import Session
from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger(__name__)

# --- Local Helper Functions (Removed circular import to trade_service) ---
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
        log.warning(f"AnalyticsService: could not convert '{value}' to Decimal.")
        return default


def _pct(entry: Any, target_price: Any, side: str) -> float:
    """Calculates PnL percentage using Decimal, returns float."""
    try:
        entry_dec = _to_decimal(entry)
        target_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite():
            return 0.0
        side_upper = (str(side.value) if hasattr(side, "value") else str(side)).upper()
        if side_upper == "LONG":
            pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT":
            pnl = ((entry_dec / target_dec) - 1) * 100
        else:
            return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError):
        return 0.0


@dataclass
class AnalyticsService:
    """
    Provides advanced user analytics.
    All methods require an external SQLAlchemy Session (Unit of Work pattern).
    """
    repo: RecommendationRepository  # instance, not class

    @staticmethod
    def _to_int_user_id(user_id: Union[int, str]) -> int:
        """Normalize Telegram user_id to integer."""
        return int(str(user_id).strip())

    @staticmethod
    def _val(x: Any, attr: str, default: Any = None) -> Any:
        """Return .attr if exists, else x itself."""
        if x is None:
            return default
        return getattr(x, attr, x)

    def win_rate_for_user(self, session: Session, user_id: Union[int, str]) -> float:
        """Calculate user's win rate for closed recommendations."""
        uid = self._to_int_user_id(user_id)
        items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        if not closed:
            return 0.0
        wins = sum(
            1
            for r in closed
            if _pct(
                _to_decimal(self._val(r.entry, "value", 0)),
                _to_decimal(r.exit_price or 0),
                self._val(r.side, "value"),
            )
            > 0
        )
        return wins * 100.0 / len(closed)

    def pnl_curve_for_user(self, session: Session, user_id: Union[int, str]) -> List[Tuple[str, float]]:
        """Return cumulative PnL% over time."""
        uid = self._to_int_user_id(user_id)
        items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        closed = [
            r for r in items
            if r.status == RecommendationStatus.CLOSED and r.exit_price is not None and r.closed_at
        ]
        closed.sort(key=lambda r: r.closed_at)
        curve, cumulative = [], 0.0
        for r in closed:
            pnl = _pct(
                _to_decimal(self._val(r.entry, "value", 0)),
                _to_decimal(r.exit_price or 0),
                self._val(r.side, "value"),
            )
            cumulative += pnl
            curve.append((r.closed_at.strftime("%Y-%m-%d"), cumulative))
        return curve

    def performance_summary_for_user(self, session: Session, user_id: Union[int, str]) -> Dict[str, Any]:
        """Return summary: totals, open, closed, win rate, and total PnL%."""
        uid = self._to_int_user_id(user_id)
        all_items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        closed = [r for r in all_items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        open_items = [r for r in all_items if r.status != RecommendationStatus.CLOSED]
        total_pnl = sum(
            _pct(
                _to_decimal(self._val(r.entry, "value", 0)),
                _to_decimal(r.exit_price or 0),
                self._val(r.side, "value"),
            )
            for r in closed
        )
        win_rate = 0.0
        if closed:
            wins = sum(
                1
                for r in closed
                if _pct(
                    _to_decimal(self._val(r.entry, "value", 0)),
                    _to_decimal(r.exit_price or 0),
                    self._val(r.side, "value"),
                )
                > 0
            )
            win_rate = wins * 100.0 / len(closed)
        return {
            "total_recommendations": len(all_items),
            "open_recommendations": len(open_items),
            "closed_recommendations": len(closed),
            "overall_win_rate": f"{win_rate:.2f}%",
            "total_pnl_percent": f"{total_pnl:.2f}%",
        }