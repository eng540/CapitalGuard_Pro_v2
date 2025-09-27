# src/capitalguard/application/services/__init__.py (Updated)
"""
تصدير جميع خدمات التطبيق
"""

from .trade_service import TradeService
from .analytics_service import AnalyticsService
from .alert_service import AlertService
from .price_service import PriceService
from .market_data_service import MarketDataService
from .autotrade_service import AutoTradeService
from .risk_service import RiskService
from .report_service import ReportService
from .audit_service import AuditService  # ✅ NEW: Import the new service

__all__ = [
    "TradeService",
    "AnalyticsService", 
    "AlertService",
    "PriceService",
    "MarketDataService",
    "AutoTradeService",
    "RiskService",
    "ReportService",
    "AuditService",  # ✅ NEW: Export the new service
]