import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any
from sqlalchemy.orm import Session
from capitalguard.infrastructure.db.performance_repository import PerformanceRepository
log = logging.getLogger(__name__)
class PerformanceService:
    def __init__(self, repo_class: type[PerformanceRepository]): self.repo_class = repo_class
    def get_trader_performance_report(self, session: Session, user_id: int) -> Dict[str, Any]:
        repo = self.repo_class(session); summary = repo.get_activated_portfolio_summary(user_id)
        if summary.get("error"):
            log.error(f"Failed to get performance report for user {user_id}: {summary.get('error')}"); return {"error": "Failed to calculate performance data."}
        total_trades = summary.get("total_trades", 0); winning_trades = summary.get("winning_trades", 0)
        aggregate_score = summary.get("aggregate_score", Decimal("0")); total_profit = summary.get("total_profit", Decimal("0")); total_loss = summary.get("total_loss", Decimal("0"))
        win_rate = (Decimal(winning_trades) / Decimal(total_trades) * 100) if total_trades > 0 else Decimal("0")
        profit_factor = Decimal("0")
        if total_profit > 0: profit_factor = Decimal("inf") if total_loss == 0 else total_profit / abs(total_loss)
        avg_trade_return = (aggregate_score / Decimal(total_trades)) if total_trades > 0 else Decimal("0")
        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": total_trades - winning_trades,
            "aggregate_score": f"{aggregate_score.quantize(Decimal('0.01'), ROUND_HALF_UP)}%",
            "win_rate_pct": f"{win_rate.quantize(Decimal('0.01'), ROUND_HALF_UP)}%",
            "profit_factor": f"{profit_factor.quantize(Decimal('0.01'), ROUND_HALF_UP)}" if profit_factor != Decimal("inf") else "Infinite",
            "avg_trade_return_pct": f"{avg_trade_return.quantize(Decimal('0.01'), ROUND_HALF_UP)}%",
            "data_source": "Activated Trade Price-Return Aggregate; not portfolio/capital return",
        }
    def get_trader_funnel_metrics(self, session: Session, user_id: int) -> Dict[str, Any]:
        metrics = self.repo_class(session).get_trader_funnel_metrics(user_id)
        if metrics.get("error"): return {"error": "Failed to calculate funnel metrics."}
        total = metrics["total_logged"]; activated = metrics["activated"]; closed = metrics["closed_activated"]
        return {**metrics, "watchlist_to_activated_rate_pct": round((activated / total) * 100, 2) if total else 0.0, "activated_to_closed_rate_pct": round((closed / activated) * 100, 2) if activated else 0.0, "data_source": "UserTrade lifecycle; performance uses additive aggregate score, not portfolio return"}
