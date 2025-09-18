# --- START OF PRODUCTION-READY FILE WITH CENTRAL LOGGING AND ERROR NOTIFICATIONS (Version 8.4.0) ---
# src/capitalguard/boot.py

import os
import logging
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


class LoggerService:
    """Centralized logger for all services with Telegram error notifications."""
    def __init__(self, notifier: Optional[TelegramNotifier] = None, name: str = "CapitalGuard"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.notifier = notifier

    def info(self, message: str):
        self.logger.info(message)

    def warning(self, message: str):
        self.logger.warning(message)

    def error(self, message: str, send_telegram: bool = True):
        self.logger.error(message)
        if send_telegram and self.notifier:
            self.notifier.send_message(f"⚠️ ERROR: {message}")


def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """
    Builds all services with centralized logging and error notification support.
    """
    # Repository and notifier
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    if ptb_app:
        notifier.set_ptb_app(ptb_app)

    # Central logger with Telegram notifications
    logger = LoggerService(notifier=notifier)

    # Binance credentials and executors
    spot_creds = BinanceCreds(
        os.getenv("BINANCE_API_KEY", ""),
        os.getenv("BINANCE_API_SECRET", "")
    )
    futu_creds = BinanceCreds(
        os.getenv("BINANCE_FUT_API_KEY", spot_creds.api_key),
        os.getenv("BINANCE_FUT_API_SECRET", spot_creds.api_secret)
    )
    exec_spot = BinanceExec(spot_creds, futures=False)
    exec_futu = BinanceExec(futu_creds, futures=True)

    # Services
    market_data_service = MarketDataService()
    price_service = PriceService()
    trade_service = TradeService(
        repo=repo,
        notifier=notifier,
        market_data_service=market_data_service,
        price_service=price_service,
        logger=logger
    )
    analytics_service = AnalyticsService(repo=repo, logger=logger)
    alert_service = AlertService(
        price_service=price_service,
        notifier=notifier,
        repo=repo,
        trade_service=trade_service,
        logger=logger
    )

    logger.info("All services initialized successfully.")

    return {
        "trade_service": trade_service,
        "analytics_service": analytics_service,
        "price_service": price_service,
        "alert_service": alert_service,
        "notifier": notifier,
        "market_data_service": market_data_service,
        "exec_spot": exec_spot,
        "exec_futu": exec_futu,
        "logger": logger,
    }


def bootstrap_app() -> Optional[Application]:
    """
    Bootstraps the Telegram bot application with:
    - Persistence
    - Service injection
    - Centralized logging
    - Error notifications
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        return None

    try:
        persistence = PicklePersistence(filepath="./telegram_bot_persistence")
        ptb_app = Application.builder() \
            .token(settings.TELEGRAM_BOT_TOKEN) \
            .persistence(persistence) \
            .build()

        # Build services and inject into bot_data
        services = build_services(ptb_app)
        ptb_app.bot_data["services"] = services

        # Register all Telegram handlers
        register_all_handlers(ptb_app)

        # Log successful bootstrap
        services["logger"].info("Telegram bot has been bootstrapped successfully.")

        return ptb_app

    except Exception as e:
        # Fallback logger if services failed to initialize
        fallback_logger = logging.getLogger("CapitalGuard")
        fallback_logger.setLevel(logging.ERROR)
        fallback_logger.addHandler(logging.StreamHandler())
        fallback_logger.error(f"Failed to bootstrap bot: {e}")
        return None

# --- END OF PRODUCTION-READY FILE WITH CENTRAL LOGGING AND ERROR NOTIFICATIONS (Version 8.4.0) ---