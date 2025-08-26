from typing import Dict, Any, List  
from capitalguard.domain.ports import RecommendationRepoPort  
from capitalguard.domain.entities import Recommendation  

class AnalyticsService:  
    def __init__(self, repo: RecommendationRepoPort) -> None:  
        self.repo = repo  

    def _calculate_pnl(self, rec: Recommendation) -> float:  
        """يحسب الربح/الخسارة كنسبة مئوية."""  
        if rec.status != "CLOSED" or rec.exit_price is None:  
            return 0.0  

        entry = rec.entry.value  
        exit_p = rec.exit_price  

        if rec.side.value == "LONG":  
            return ((exit_p - entry) / entry) * 100  
        elif rec.side.value == "SHORT":  
            return ((entry - exit_p) / entry) * 100  
        return 0.0  

    def performance_summary(self, channel_id: int | None = None) -> Dict[str, Any]:  
        all_recs = self.repo.list_all(channel_id)  
        closed_recs = [r for r in all_recs if r.status == "CLOSED"]  

        if not closed_recs:  
            return {  
                "total_closed_trades": 0,  
                "win_rate_percent": 0,  
                "total_pnl_percent": 0,  
                "average_pnl_percent": 0,  
                "best_trade_pnl_percent": 0,  
                "worst_trade_pnl_percent": 0,  
            }  

        pnl_list = [self._calculate_pnl(rec) for rec in closed_recs]  

        wins = sum(1 for pnl in pnl_list if pnl > 0)  
        win_rate = (wins / len(closed_recs)) * 100 if closed_recs else 0  

        total_pnl = sum(pnl_list)  

        return {  
            "total_closed_trades": len(closed_recs),  
            "win_rate_percent": round(win_rate, 2),  
            "total_pnl_percent": round(total_pnl, 2),  
            "average_pnl_percent": round(total_pnl / len(closed_recs), 2),  
            "best_trade_pnl_percent": round(max(pnl_list), 2),  
            "worst_trade_pnl_percent": round(min(pnl_list), 2),  
        }