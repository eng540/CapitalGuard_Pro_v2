from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Union
from decimal import Decimal, InvalidOperation
import logging
from sqlalchemy.orm import Session
from capitalguard.domain.entities import RecommendationStatus
from capitalguard.domain.financial_metrics import price_return_pct
from capitalguard.infrastructure.db.repository import RecommendationRepository
log = logging.getLogger(__name__)
def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    if isinstance(value, Decimal): return value if value.is_finite() else default
    if value is None: return default
    try:
        d = Decimal(str(value)); return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError): return default
@dataclass
class AnalyticsService:
    repo: RecommendationRepository
    @staticmethod
    def _to_int_user_id(user_id: Union[int, str]) -> int: return int(str(user_id).strip())
    @staticmethod
    def _val(x: Any, attr: str, default: Any = None) -> Any:
        if x is None: return default
        return getattr(x, attr, x)
    def win_rate_for_user(self, session: Session, user_id: Union[int, str]) -> float:
        uid = self._to_int_user_id(user_id); items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        if not closed: return 0.0
        wins = sum(1 for r in closed if float(price_return_pct(_to_decimal(self._val(r.entry, 'value', 0)), _to_decimal(r.exit_price or 0), self._val(r.side, 'value'))) > 0)
        return wins * 100.0 / len(closed)
    def pnl_curve_for_user(self, session: Session, user_id: Union[int, str]) -> List[Tuple[str, float]]:
        uid = self._to_int_user_id(user_id); items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None and r.closed_at]; closed.sort(key=lambda r: r.closed_at)
        curve, cumulative_pnl = [], 0.0
        for r in closed:
            pnl = float(price_return_pct(_to_decimal(self._val(r.entry, 'value', 0)), _to_decimal(r.exit_price or 0), self._val(r.side, 'value')))
            cumulative_pnl += pnl; curve.append((r.closed_at.strftime('%Y-%m-%d'), cumulative_pnl))
        return curve
    def performance_summary_for_user(self, session: Session, user_id: Union[int, str]) -> Dict[str, Any]:
        uid = self._to_int_user_id(user_id); all_items = self.repo.list_all_for_user(session, user_telegram_id=uid)
        closed_items = [r for r in all_items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        open_items = [r for r in all_items if r.status != RecommendationStatus.CLOSED]
        total_pnl = sum(
            float(price_return_pct(_to_decimal(self._val(r.entry, 'value', 0)), _to_decimal(r.exit_price or 0), self._val(r.side, 'value')))
            for r in closed_items
        )
        wins = sum(
            1
            for r in closed_items
            if float(price_return_pct(_to_decimal(self._val(r.entry, 'value', 0)), _to_decimal(r.exit_price or 0), self._val(r.side, 'value'))) > 0
        )
        win_rate = wins * 100.0 / len(closed_items) if closed_items else 0.0
        return {'total_recommendations': len(all_items), 'open_recommendations': len(open_items), 'closed_recommendations': len(closed_items), 'overall_win_rate': f'{win_rate:.2f}%', 'aggregate_score': f'{total_pnl:.2f}%'}
