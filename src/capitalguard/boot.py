# --- START OF FINAL, PRODUCTION-READY FILE (Version 15.1.0) ---
# src/capitalguard/boot.py

import os
import logging
import sys
from typing import Dict, Any, Optional
import threading

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
from capitalguard.service_registry import register_global_services

class TelegramLogHandler(logging.Handler):
    """
    A custom logging handler that sends critical messages to a Telegram chat.
    ✅ FIX: Now includes a recursion lock to prevent infinite loops if the
    notifier itself fails.
    """
    def __init__(self, notifier: TelegramNotifier, level=logging.ERROR):
        super().__init__(level=level)
        self.notifier = notifier
        # Thread-local storage for the recursion lock flag
        self._local = threading.local()
        self._local.is_handling = False

    def emit(self, record: logging.LogRecord):
        # If we are already in the process of handling a log, exit to prevent recursion.
        if getattr(self._local, 'is_handling', False):
            return
        
        if not self.notifier or not settings.TELEGRAM_ADMIN_CHAT_ID:
            return
        
        try:
            # Set the lock
            self._local.is_handling = True
            
            simple_message = f"⚠️ CRITICAL ERROR: {record.getMessage()}"
            
            admin_chat_id = int(settings.TELEGRAM_ADMIN_CHAT_ID)
            if hasattr(self.notifier, 'send_private_text'):
                # This is a synchronous call from a potentially async context,
                # which is acceptable for logging critical errors.
                self.notifier.send_private_text(chat_id=admin_chat_id, text=simple_message)
        except Exception as e:
            # If sending the log fails, log it to the standard output instead of
            # re-triggering this handler.
            # Use the root logger to prevent recursion.
            root_logger = logging.getLogger()
            # Temporarily remove this handler to log the failure without looping
            root_logger.removeHandler(self)
            root_logger.error(f"CRITICAL: Failed to send log to Telegram: {e}", exc_info=False)
            root_logger.addHandler(self)
        finally:
            # Always release the lock
            self._local.is_handling = False

def setup_logging(notifier: Optional[TelegramNotifier] = None) -> None:
    """Configures the root logger for the entire application."""
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        # Clear existing handlers to avoid duplicates, especially in dev with reloads
        root_logger.handlers.clear()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    if notifier and settings.TELEGRAM_ADMIN_CHAT_ID:
        telegram_handler = TelegramLogHandler(notifier)
        telegram_handler.setLevel(logging.ERROR)
        root_logger.addHandler(telegram_handler)
        logging.info("TelegramLogHandler configured for admin notifications.")
    else:
        logging.warning("TelegramLogHandler is not configured (TELEGRAM_ADMIN_CHAT_ID not set).")


    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.info("Logging configured successfully.")

def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """Builds all services and populates the global registry."""
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    if ptb_app:
        notifier.set_ptb_app(ptb_app)
    
    # Setup logging early, potentially with the notifier for error reporting
    setup_logging(notifier)

    spot_creds = BinanceCreds(os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))
    futu_creds = BinanceCreds(os.getenv("BINANCE_FUT_API_KEY", spot_creds.api_key), os.getenv("BINANCE_FUT_API_SECRET", spot_creds.api_secret))
    exec_spot = BinanceExec(spot_creds, futures=False)
    exec_futu = BinanceExec(futu_creds, futures=True)

    market_data_service = MarketDataService()
    price_service = PriceService()
    trade_service = TradeService(repo=repo, notifier=notifier, market_data_service=market_data_service, price_service=price_service)
    analytics_service = AnalyticsService(repo=repo)
    
    alert_service = AlertService(trade_service=trade_service, repo=repo)

    services = {
        "trade_service": trade_service,
        "analytics_service": analytics_service,
        "price_service": price_service,
        "alert_service": alert_service,
        "notifier": notifier,
        "market_data_service": market_data_service,
    }

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
        
        services = build_services(ptb_app)
        ptb_app.bot_data["services"] = services

        register_all_handlers(ptb_app)
        logging.info("Telegram bot bootstrapped successfully.")
        return ptb_app
    except Exception as e:
        logging.exception(f"CRITICAL: Failed to bootstrap bot: {e}")
        return None

# --- END OF FINAL, PRODUCTION-READY FILE (Version 15.1.0) ---