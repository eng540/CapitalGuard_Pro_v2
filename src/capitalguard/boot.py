# --- START OF FILE: src/capitalguard/boot.py ---
from __future__ import annotations
from dataclasses import dataclass
import os

from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.risk_service import RiskService
from capitalguard.application.services.autotrade_service import AutoTradeService
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

@dataclass
class ServicesPack:
    repo: RecommendationRepository
    notifier: TelegramNotifier
    trade_service: TradeService
    report_service: ReportService
    analytics_service: AnalyticsService
    price_service: PriceService
    alert_service: AlertService
    risk_service: RiskService
    autotrade_service: AutoTradeService

def build_services() -> dict:
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    trade = TradeService(repo=repo, notifier=notifier)
    report = ReportService(repo=repo)
    analytics = AnalyticsService(repo=repo)
    price = PriceService()

    # Binance creds
    spot_creds = BinanceCreds(api_key=os.getenv("BINANCE_API_KEY",""), api_secret=os.getenv("BINANCE_API_SECRET",""))
    futu_creds = BinanceCreds(api_key=os.getenv("BINANCE_FUT_API_KEY", os.getenv("BINANCE_API_KEY","")),
                              api_secret=os.getenv("BINANCE_FUT_API_SECRET", os.getenv("BINANCE_API_SECRET","")))
    exec_spot = BinanceExec(spot_creds, futures=False)
    exec_futu = BinanceExec(futu_creds, futures=True)

    risk = RiskService(exec_spot=exec_spot, exec_futu=exec_futu)
    autotrade = AutoTradeService(repo=repo, notifier=notifier, risk=risk, exec_spot=exec_spot, exec_futu=exec_futu)
    alert = AlertService(price_service=price, notifier=notifier, repo=repo, trade_service=trade)

    return {
        "repo": repo,
        "notifier": notifier,
        "trade_service": trade,
        "report_service": report,
        "analytics_service": analytics,
        "price_service": price,
        "alert_service": alert,
        "risk_service": risk,
        "autotrade_service": autotrade,
    }
# --- END OF FILE ---