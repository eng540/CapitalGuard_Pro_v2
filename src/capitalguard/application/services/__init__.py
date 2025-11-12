# src/capitalguard/application/services/__init__.py (Updated for ADR-003)
"""
تصدير جميع خدمات التطبيق
✅ THE FIX (ADR-003): Added ImageParsingService to the exports list
to make it discoverable by the application bootstrapper.
"""

from .trade_service import TradeService
from .analytics_service import AnalyticsService
from .alert_service import AlertService
from .price_service import PriceService
from .market_data_service import MarketDataService
from .autotrade_service import AutoTradeService
from .risk_service import RiskService
from .report_service import ReportService
from .audit_service import AuditService
from .image_parsing_service import ImageParsingService  # ✅ NEW: خدمة تحليل الصور

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
    "ImageParsingService",  # ✅ NEW: خدمة تحليل الصور
]