import logging
from typing import List, Dict, Any
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select, func, case
from capitalguard.infrastructure.db.models import UserTrade, UserTradeStatusEnum
log = logging.getLogger(__name__)
class PerformanceRepository:
    """Repository for activated-trade performance data.

    `aggregate_score` is the additive sum of trade-level unleveraged price-return
    percentages. It is deliberately not a portfolio/capital return.
    """
    def __init__(self, session: Session): self.session = session
    def get_closed_activated_trades_for_user(self, user_id: int) -> List[UserTrade]:
        try:
            stmt = select(UserTrade).where(UserTrade.user_id == user_id, UserTrade.status == UserTradeStatusEnum.CLOSED, UserTrade.activated_at.isnot(None), UserTrade.pnl_percentage.isnot(None))
            return self.session.execute(stmt).scalars().all()
        except Exception as e:
            log.error(f"Error fetching performance data for user {user_id}: {e}", exc_info=True); return []
    def get_trader_funnel_metrics(self, user_id: int) -> Dict[str, int]:
        try:
            def count_where(*conditions) -> int:
                stmt = select(func.count(UserTrade.id)).where(UserTrade.user_id == user_id, *conditions)
                return int(self.session.execute(stmt).scalar_one() or 0)
            total_logged = count_where(); direct_logged = count_where(UserTrade.source_type == "DIRECT_INPUT"); activated = count_where(UserTrade.activated_at.isnot(None)); closed_activated = count_where(UserTrade.status == UserTradeStatusEnum.CLOSED, UserTrade.activated_at.isnot(None), UserTrade.pnl_percentage.isnot(None))
            return {"total_logged": total_logged, "direct_input_logged": direct_logged, "forward_logged": max(total_logged - direct_logged, 0), "activated": activated, "closed_activated": closed_activated}
        except Exception as e:
            log.error(f"Error calculating funnel metrics for user {user_id}: {e}", exc_info=True); return {"error": str(e)}
    def get_activated_portfolio_summary(self, user_id: int) -> Dict[str, Any]:
        try:
            cte = select(UserTrade.pnl_percentage).where(UserTrade.user_id == user_id, UserTrade.status == UserTradeStatusEnum.CLOSED, UserTrade.activated_at.isnot(None), UserTrade.pnl_percentage.isnot(None)).cte("activated_closed_trades")
            stmt = select(
                func.count(cte.c.pnl_percentage).label("total_trades"),
                func.sum(case((cte.c.pnl_percentage > 0, 1), else_=0)).label("winning_trades"),
                func.sum(cte.c.pnl_percentage).label("aggregate_score"),
                func.sum(case((cte.c.pnl_percentage > 0, cte.c.pnl_percentage), else_=0)).label("total_profit"),
                func.sum(case((cte.c.pnl_percentage < 0, cte.c.pnl_percentage), else_=0)).label("total_loss"),
            ).select_from(cte)
            result = self.session.execute(stmt).first()
            if result and result.total_trades > 0: return dict(result._mapping)
            return {"total_trades": 0, "winning_trades": 0, "aggregate_score": Decimal("0"), "total_profit": Decimal("0"), "total_loss": Decimal("0")}
        except Exception as e:
            log.error(f"Error calculating performance summary for user {user_id}: {e}", exc_info=True); return {"error": str(e)}
