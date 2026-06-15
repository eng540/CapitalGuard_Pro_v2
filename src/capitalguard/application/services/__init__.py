# File: src/capitalguard/application/services/__init__.py
# Version: v3.0.1-R2
# ✅ THE FIX: (R2 Architecture)
#    - 1. (NEW) إضافة `PerformanceService`, `CreationService`, `LifecycleService`
#       إلى قائمة التصدير `__all__` لجعلها متاحة لـ `boot.py`.
#    - 2. (v3.0.1) إزالة علامات الاقتباس.
# 🎯 IMPACT: اكتمال تسجيل الخدمات الجديدة في النظام.

from .trade_service import TradeService
from .analytics_service import AnalyticsService
from .alert_service import AlertService
from .price_service import PriceService
from .market_data_service import MarketDataService
from .autotrade_service import AutoTradeService
from .risk_service import RiskService
from .report_service import ReportService
from .audit_service import AuditService
from .image_parsing_service import ImageParsingService
# ✅ NEW (R2):
from .performance_service import PerformanceService
from .creation_service import CreationService
from .lifecycle_service import LifecycleService

__all__ = [
    "TradeService",
    "AnalyticsService", 
    "AlertService",
    "PriceService",
    "MarketDataService",
    "AutoTradeService",
    "RiskService",
    "ReportService",
    "AuditService",
    "ImageParsingService",
    # ✅ NEW (R2):
    "PerformanceService",
    "CreationService",
    "LifecycleService",
]