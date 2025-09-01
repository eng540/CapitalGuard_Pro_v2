#--- START OF FILE: src/capitalguard/boot.py ---
import os
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.risk_service import RiskService
from capitalguard.application.services.autotrade_service import AutoTradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds
from capitalguard.infrastructure.pricing.binance import BinancePricing

def build_services() -> dict:
    """
    Composition Root: يبني كل الخدمات مرة واحدة ويعيدها في قاموس.
    """
    # Infrastructure Components
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    price_provider = BinancePricing() # مزود السعر الفعلي

    # Binance Execution Credentials
    spot_creds = BinanceCreds(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", "")
    )
    futu_creds = BinanceCreds(
        api_key=os.getenv("BINANCE_FUT_API_KEY", spot_creds.api_key),
        api_secret=os.getenv("BINANCE_FUT_API_SECRET", spot_creds.api_secret)
    )
    exec_spot = BinanceExec(spot_creds, futures=False)
    exec_futu = BinanceExec(futu_creds, futures=True)

    # Application Services
    trade_service = TradeService(repo=repo, notifier=notifier)
    report_service = ReportService(repo=repo)
    analytics_service = AnalyticsService(repo=repo)
    price_service = PriceService(price_provider=price_provider)
    risk_service = RiskService(exec_spot=exec_spot, exec_futu=exec_futu)
    autotrade_service = AutoTradeService(
        repo=repo, notifier=notifier, risk_service=risk_service,
        exec_spot=exec_spot, exec_futu=exec_futu
    )
    alert_service = AlertService(
        price_service=price_service, notifier=notifier,
        repo=repo, trade_service=trade_service
    )

    return {
        "trade_service": trade_service,
        "report_service": report_service,
        "analytics_service": analytics_service,
        "price_service": price_service,
        "alert_service": alert_service,
        "risk_service": risk_service,
        "autotrade_service": autotrade_service,
    }
#--- END OF FILE ---