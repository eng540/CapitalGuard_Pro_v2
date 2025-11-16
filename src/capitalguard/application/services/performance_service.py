# File: src/capitalguard/application/services/performance_service.py
# Version: v3.0.1-R2
# ✅ THE FIX: (NEW FILE - R2 Architecture)
#    - 1. (NEW) خدمة جديدة ومستقلة مخصصة لحسابات الأداء.
#    - 2. (SoC) تفصل منطق حساب PnL/WinRate عن `analytics_service` و `trade_service`.
#    - 3. (Core Algorithm) تنفذ "العقد التشغيلي"
#       عن طريق الاعتماد *فقط* على `PerformanceRepository` لجلب بيانات "المحفظة المفعلة".
# 🎯 IMPACT: هذا هو المحرك الحسابي الجديد للمرحلة R2، مما يجعل التقارير دقيقة وموثوقة.

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
from capitalguard.infrastructure.db.performance_repository import PerformanceRepository

log = logging.getLogger(__name__)

class PerformanceService:
    """
    [R2 Feature]
    الخدمة المسؤولة عن حساب مؤشرات الأداء الرئيسية (KPIs)
    بناءً على "العقد التشغيلي" (الاعتماد على المحفظة المفعلة فقط).
    """

    def __init__(self, repo_class: type[PerformanceRepository]):
        self.repo_class = repo_class

    def get_trader_performance_report(self, session: Session, user_id: int) -> Dict[str, Any]:
        """
        [الخوارزمية الأساسية - R2]
        إنشاء تقرير أداء كامل للمتداول بناءً على صفقاته "المفعلة" المغلقة.
        """
        repo = self.repo_class(session)
        summary = repo.get_activated_portfolio_summary(user_id)
        
        if summary.get("error"):
            log.error(f"Failed to get performance report for user {user_id}: {summary.get('error')}")
            return {"error": "Failed to calculate performance data."}

        total_trades = summary.get("total_trades", 0)
        winning_trades = summary.get("winning_trades", 0)
        total_pnl_pct = summary.get("total_pnl_pct", Decimal("0"))
        total_profit = summary.get("total_profit", Decimal("0"))
        total_loss = summary.get("total_loss", Decimal("0")) # This will be negative or zero

        # --- حساب المؤشرات ---

        # 1. Win Rate (نسبة الفوز)
        win_rate = (Decimal(winning_trades) / Decimal(total_trades) * 100) if total_trades > 0 else Decimal("0")

        # 2. Profit Factor (معامل الربح)
        profit_factor = Decimal("0")
        if total_profit > 0:
            if total_loss == 0:
                profit_factor = Decimal("inf") # ربح بلا خسارة
            else:
                profit_factor = total_profit / abs(total_loss)

        # 3. Average PnL (متوسط الربح/الخسارة)
        avg_pnl_pct = (total_pnl_pct / Decimal(total_trades)) if total_trades > 0 else Decimal("0")

        # --- تجميع التقرير ---
        report = {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": total_trades - winning_trades,
            "total_pnl_pct": f"{total_pnl_pct.quantize(Decimal('0.01'), ROUND_HALF_UP)}%",
            "win_rate_pct": f"{win_rate.quantize(Decimal('0.01'), ROUND_HALF_UP)}%",
            "profit_factor": f"{profit_factor.quantize(Decimal('0.01'), ROUND_HALF_UP)}" if profit_factor != Decimal("inf") else "Infinite",
            "avg_pnl_pct": f"{avg_pnl_pct.quantize(Decimal('0.01'), ROUND_HALF_UP)}%",
            "data_source": "Activated Portfolio Only"
        }
        
        return report

    # ... يمكن إضافة وظائف لحساب أداء المحلل هنا في المستقبل ...
    # def get_analyst_performance_report(self, session: Session, analyst_id: int) -> Dict[str, Any]:
    #    ...