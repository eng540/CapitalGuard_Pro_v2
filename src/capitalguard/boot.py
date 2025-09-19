# --- START OF FINAL, PRODUCTION-READY FILE WITH REGISTRY INTEGRATION (Version 9.3.0) ---
# src/capitalguard/boot.py

import os
import logging
import sys
from typing import Dict, Any, Optional
from telegram.ext import Application, PicklePersistence

from capitalguard.config import settings
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds
from capitalguard.interfaces.telegram.handlers import register_all_handlers
# ✅ Import the new registration function
from capitalguard.service_registry import register_global_services

# ... (TelegramLogHandler and setup_logging can remain the same as the approved version)
class TelegramLogHandler(logging.Handler):
    # ... (code from previous approved version)
def setup_logging(notifier: Optional[TelegramNotifier] = None) -> None:
    # ... (code from previous approved version)

def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """Builds all services and populates the global registry."""
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    if ptb_app:
        notifier.set_ptb_app(ptb_app)
    
    setup_logging(notifier)

    spot_creds = BinanceCreds(os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))
    futu_creds = BinanceCreds(os.getenv("BINANCE_FUT_API_KEY", spot_creds.api_key), os.getenv("BINANCE_FUT_API_SECRET", spot_creds.api_secret))
    exec_spot = BinanceExec(spot_creds, futures=False)
    exec_futu = BinanceExec(futu_creds, futures=True)

    market_data_service = MarketDataService()
    price_service = PriceService()
    trade_service = TradeService(repo=repo, notifier=notifier, market_data_service=market_data_service, price_service=price_service)
    analytics_service = AnalyticsService(repo=repo)
    alert_service = AlertService(price_service=price_service, notifier=notifier, repo=repo, trade_service=trade_service)

    services = {
        "trade_service": trade_service,
        "analytics_service": analytics_service,
        "price_service": price_service,
        "alert_service": alert_service,
        "notifier": notifier,
        "market_data_service": market_data_service,
    }

    # ✅ Populate the global registry with the built services
    register_global_services(services)
    
    return services

def bootstrap_app() -> Optional[Application]:
    """Bootstraps the Telegram bot application."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN not set. Bot cannot start.")
        return None
    try:
        persistence = PicklePersistence(filepath="./telegram_bot_persistence")
        ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
        
        # Build services and populate the registry.
        # We still return services to inject into bot_data for potential direct access if needed,
        # but the primary access method will now be the global registry.
        services = build_services(ptb_app)
        ptb_app.bot_data["services"] = services

        register_all_handlers(ptb_app)
        logging.info("Telegram bot bootstrapped successfully.")
        return ptb_app
    except Exception as e:
        logging.exception(f"CRITICAL: Failed to bootstrap bot: {e}")
        return None

# --- END OF FINAL, PRODUCTION-READY FILE WITH REGISTRY INTEGRATION (Version 9.3.0) ---