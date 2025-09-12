# --- START OF MODIFIED FILE: src/capitalguard/boot.py ---
import os
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.risk_service import RiskService
from capitalguard.application.services.autotrade_service import AutoTradeService
# ✅ --- 1. استيراد الخدمة الجديدة ---
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

def build_services() -> dict:
    """
    Composition Root: Builds all services once and returns them in a dictionary.
    """
    # Infrastructure Components
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    
    spot_creds = BinanceCreds(...)
    futu_creds = BinanceCreds(...)
    exec_spot = BinanceExec(...)
    exec_futu = BinanceExec(...)

    # ✅ --- 2. إنشاء نسخة من الخدمة الجديدة ---
    market_data_service = MarketDataService()

    # Application Services
    price_service = PriceService()
    # ✅ --- 3. حقن الخدمة الجديدة في TradeService ---
    trade_service = TradeService(repo=repo, notifier=notifier, market_data_service=market_data_service)
    report_service = ReportService(repo=repo)
    analytics_service = AnalyticsService(repo=repo)
    risk_service = RiskService(exec_spot=exec_spot, exec_futu=exec_futu)
    autotrade_service = AutoTradeService(...)
    alert_service = AlertService(...)

    return {
        "trade_service": trade_service,
        "report_service": report_service,
        "analytics_service": analytics_service,
        "price_service": price_service,
        "alert_service": alert_service,
        "risk_service": risk_service,
        "autotrade_service": autotrade_service,
        "notifier": notifier,
        # ✅ --- 4. إضافة الخدمة إلى القاموس العام ---
        "market_data_service": market_data_service,
    }
# --- END OF MODIFIED FILE ---