# --- START OF FILE: src/capitalguard/application/services/analytics_service.py ---
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple, Dict, Any
from datetime import datetime
from math import isfinite
from capitalguard.domain.entities import RecommendationStatus

@dataclass
class AnalyticsService:
    """تحليلات متقدمة دون تعديل مخطط DB."""
    repo: any  # RecommendationRepository

    @staticmethod
    def _pnl_percent(side: str, entry: float, exit_price: float) -> float:
        s = (side or "").upper()
        if not entry or not exit_price or entry == 0:
            return 0.0
        if s == "LONG":
            return (exit_price / entry - 1.0) * 100.0
        return (entry / exit_price - 1.0) * 100.0

    @staticmethod
    def _rr(entry: float, sl: float, tp1: Optional[float], side: str) -> Optional[float]:
        try:
            if tp1 is None: return None
            risk = abs(entry - sl)
            reward = abs((tp1 - entry)) if (side.upper() == "LONG") else abs((entry - tp1))
            if risk <= 0 or reward <= 0:
                return None
            r = reward / risk
            return r if isfinite(r) else None
        except Exception:
            return None

    def list_filtered(
        self,
        user_id: Optional[str] = None,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        market: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> List:
        # ✅ FIX: Delegate the most common filters (symbol, status) to the database.
        # This prevents loading the entire table into memory.
        items = self.repo.list_all(symbol=symbol, status=status)
        
        # Apply remaining filters in Python
        res = []
        for r in items:
            if user_id is not None and str(getattr(r, "user_id", None)) != str(user_id):
                continue
            if market:
                m = str(getattr(getattr(r, "market", None), "value", getattr(r, "market", ""))).lower()
                if market.lower() not in m:
                    continue
            if date_from and getattr(r, "created_at", None) and r.created_at < date_from:
                continue
            if date_to and getattr(r, "created_at", None) and r.created_at > date_to:
                continue
            res.append(r)
        return res

    def win_rate(self, items: Iterable) -> float:
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        if not closed:
            return 0.0
        wins = 0
        for r in closed:
            pnl = self._pnl_percent(getattr(r.side, "value", r.side), float(getattr(r.entry, "value", r.entry)), float(r.exit_price))
            if pnl > 0:
                wins += 1
        return wins * 100.0 / len(closed)

    def total_pnl_by_user(self, user_id: Optional[str]) -> float:
        items = self.list_filtered(user_id=user_id)
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        s = 0.0
        for r in closed:
            s += self._pnl_percent(getattr(r.side, "value", r.side), float(getattr(r.entry, "value", r.entry)), float(r.exit_price))
        return s

    def pnl_curve(self, items: Iterable) -> List[Tuple[str, float]]:
        closed = [r for r in items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None and r.closed_at]
        closed.sort(key=lambda r: r.closed_at)
        curve, c = [], 0.0
        for r in closed:
            pnl = self._pnl_percent(getattr(r.side, "value", r.side), float(getattr(r.entry, "value", r.entry)), float(r.exit_price))
            c += pnl
            day = r.closed_at.strftime("%Y-%m-%d")
            curve.append((day, c))
        return curve

    def summary_by_market(self, items: Iterable) -> Dict[str, Dict[str, float]]:
        buckets: Dict[str, List] = {}
        for r in items:
            m = str(getattr(getattr(r, "market", None), "value", getattr(r, "market", "Unknown")))
            buckets.setdefault(m, []).append(r)
        out: Dict[str, Dict[str, float]] = {}
        for m, arr in buckets.items():
            out[m] = {
                "count": float(len(arr)),
                "win_rate": self.win_rate(arr),
                "sum_pnl": sum(
                    self._pnl_percent(getattr(x.side, "value", x.side), float(getattr(x.entry, "value", x.entry)), float(getattr(x, "exit_price", 0) or 0))
                    for x in arr if x.status == RecommendationStatus.CLOSED and x.exit_price is not None
                ),
            }
        return out

    def rr_actual(self, r) -> Optional[float]:
        try:
            side = getattr(r.side, "value", r.side)
            entry = float(getattr(r.entry, "value", r.entry))
            sl    = float(getattr(r.stop_loss, "value", r.stop_loss))
            tps   = list(getattr(r.targets, "values", r.targets or []))
            tp1   = float(tps[0]) if tps else None
            base_rr = self._rr(entry, sl, tp1, side)
            if r.exit_price is None or base_rr is None:
                return None
            reward = abs((r.exit_price - entry)) if side.upper()=="LONG" else abs((entry - r.exit_price))
            risk   = abs(entry - sl)
            if risk <= 0: return None
            rr = reward / risk
            return rr if isfinite(rr) else None
        except Exception:
            return None
    
    def performance_summary(self) -> Dict[str, Any]:
        """
        تجمع ملخصًا شاملاً للأداء العام.
        """
        all_items = self.repo.list_all()
        closed_items = [r for r in all_items if r.status == RecommendationStatus.CLOSED and r.exit_price is not None]
        open_items = [r for r in all_items if r.status != RecommendationStatus.CLOSED]
        
        total_pnl = sum(
            self._pnl_percent(
                getattr(r.side, "value", r.side), 
                float(getattr(r.entry, "value", r.entry)), 
                float(r.exit_price)
            ) for r in closed_items
        )

        return {
            "total_recommendations": len(all_items),
            "open_recommendations": len(open_items),
            "closed_recommendations": len(closed_items),
            "overall_win_rate": f"{self.win_rate(closed_items):.2f}%",
            "total_pnl_percent": f"{total_pnl:.2f}%",
        }
# --- END OF FILE ---