# File: src/capitalguard/infrastructure/db/performance_repository.py
# Version: v3.0.0-R2
# ✅ THE FIX: (NEW FILE - R2 Architecture)
#    - 1. (NEW) إنشاء مستودع (Repository) جديد ومستقل تمامًا.
#    - 2. (SoC) فصل منطق استعلامات الأداء المعقدة عن المستودعات الأخرى.
# 🎯 IMPACT: هذا الملف هو "مصدر الحقيقة" (SSoT) لجلب البيانات المالية للمتداول،
#    مع الالتزام الصارم بخوارزمية "المحفظة المفعلة" (Activated Portfolio).

import logging
from typing import List, Dict, Any, Optional
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, case

from capitalguard.infrastructure.db.models import UserTrade, UserTradeStatusEnum, User

log = logging.getLogger(__name__)

class PerformanceRepository:
    """
    مستودع متخصص لجلب بيانات الأداء المحسوبة.
    يركز حصريًا على الصفقات التي دخلت "المحفظة المفعلة".
    """
    def __init__(self, session: Session):
        self.session = session

    def get_closed_activated_trades_for_user(self, user_id: int) -> List[UserTrade]:
        """
        [الخوارزمية الأساسية]
        جلب جميع صفقات المتداول المغلقة (CLOSED) التي كانت "مفعلة" (ACTIVATED) في وقت ما.
        هذا هو مصدر الحقيقة الوحيد لحساب PnL و Win Rate.
        
        لماذا؟
        - نحن نتجاهل "WATCHLIST" لأن المتداول لم "يدخل" فيها.
        - نحن نتجاهل "PENDING_ACTIVATION" التي أُغلقت (INVALIDATED) لأنها لم تُفعّل أبدًا.
        """
        try:
            # للتأكد من أننا نحسب فقط الصفقات التي تم "الدخول فيها"،
            # سنقوم بجلب الصفقات المغلقة التي تحتوي على 'activated_at' (تاريخ تفعيل).
            
            stmt = (
                select(UserTrade)
                .where(
                    UserTrade.user_id == user_id,
                    UserTrade.status == UserTradeStatusEnum.CLOSED,
                    # هذا هو الشرط الجوهري لـ "المحفظة المفعلة"
                    UserTrade.activated_at.isnot(None),
                    UserTrade.pnl_percentage.isnot(None) # التأكد من أن PnL قد حُسب
                )
            )
            
            trades = self.session.execute(stmt).scalars().all()
            log.debug(f"Found {len(trades)} closed 'activated' trades for user {user_id}")
            return trades

        except Exception as e:
            log.error(f"Error fetching performance data for user {user_id}: {e}", exc_info=True)
            return []

    def get_trader_funnel_metrics(self, user_id: int) -> Dict[str, int]:
        """Return lifecycle counts for one trader without mixing recommendation data."""
        try:
            def count_where(*conditions) -> int:
                stmt = select(func.count(UserTrade.id)).where(
                    UserTrade.user_id == user_id,
                    *conditions,
                )
                return int(self.session.execute(stmt).scalar_one() or 0)

            total_logged = count_where()
            direct_logged = count_where(UserTrade.source_type == "DIRECT_INPUT")
            activated = count_where(UserTrade.activated_at.isnot(None))
            closed_activated = count_where(
                UserTrade.status == UserTradeStatusEnum.CLOSED,
                UserTrade.activated_at.isnot(None),
                UserTrade.pnl_percentage.isnot(None),
            )
            return {
                "total_logged": total_logged,
                "direct_input_logged": direct_logged,
                "forward_logged": max(total_logged - direct_logged, 0),
                "activated": activated,
                "closed_activated": closed_activated,
            }
        except Exception as e:
            log.error(f"Error calculating funnel metrics for user {user_id}: {e}", exc_info=True)
            return {"error": str(e)}

    def get_activated_portfolio_summary(self, user_id: int) -> Dict[str, Any]:
        """
        [الخوارزمية الأساسية]
        يقوم بإجراء استعلام مجمّع (Aggregate Query) فعال لحساب
        Win Rate, Total PnL, و Profit Factor مباشرة من قاعدة البيانات.
        """
        try:
            # بناء الاستعلام الفرعي (CTE) الذي يطابق منطق "المحفظة المفعلة"
            activated_closed_trades_cte = (
                select(
                    UserTrade.pnl_percentage
                )
                .where(
                    UserTrade.user_id == user_id,
                    UserTrade.status == UserTradeStatusEnum.CLOSED,
                    UserTrade.activated_at.isnot(None),
                    UserTrade.pnl_percentage.isnot(None)
                )
                .cte("activated_closed_trades")
            )

            # الاستعلام المجمّع
            stmt = (
                select(
                    # 1. إجمالي عدد الصفقات
                    func.count(activated_closed_trades_cte.c.pnl_percentage).label("total_trades"),
                    
                    # 2. عدد الصفقات الرابحة
                    func.sum(
                        case(
                            (activated_closed_trades_cte.c.pnl_percentage > 0, 1),
                            else_=0
                        )
                    ).label("winning_trades"),
                    
                    # 3. إجمالي PnL (كمجموع نسب مئوية)
                    func.sum(activated_closed_trades_cte.c.pnl_percentage).label("total_pnl_pct"),
                    
                    # 4. إجمالي الربح (لحساب Profit Factor)
                    func.sum(
                        case(
                            (activated_closed_trades_cte.c.pnl_percentage > 0, activated_closed_trades_cte.c.pnl_percentage),
                            else_=0
                        )
                    ).label("total_profit"),
                    
                    # 5. إجمالي الخسارة (لحساب Profit Factor)
                    func.sum(
                        case(
                            (activated_closed_trades_cte.c.pnl_percentage < 0, activated_closed_trades_cte.c.pnl_percentage),
                            else_=0
                        )
                    ).label("total_loss")
                )
                .select_from(activated_closed_trades_cte)
            )
            
            result = self.session.execute(stmt).first()
            
            if result and result.total_trades > 0:
                # _mapping attribute is available on SQLAlchemy 1.4+ Row objects
                return dict(result._mapping)
            
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "total_pnl_pct": Decimal("0"),
                "total_profit": Decimal("0"),
                "total_loss": Decimal("0")
            }

        except Exception as e:
            log.error(f"Error calculating portfolio summary for user {user_id}: {e}", exc_info=True)
            return {"error": str(e)}