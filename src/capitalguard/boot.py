# --- START OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.0) ---
# src/capitalguard/boot.py

import os
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

def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """
    Composition Root: Builds all services once and returns them in a dictionary.
    This is where all dependencies are wired together.
    """
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    if ptb_app:
        # Inject the application context into the notifier so it can access
        # bot information like the username.
        notifier.set_ptb_app(ptb_app)

    spot_creds = BinanceCreds(os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))
    futu_creds = BinanceCreds(os.getenv("BINANCE_FUT_API_KEY", spot_creds.api_key), os.getenv("BINANCE_FUT_API_SECRET", spot_creds.api_secret))
    exec_spot = BinanceExec(spot_creds, futures=False)
    exec_futu = BinanceExec(futu_creds, futures=True)

    market_data_service = MarketDataService()
    price_service = PriceService()
    trade_service = TradeService(
        repo=repo,
        notifier=notifier,
        market_data_service=market_data_service,
        price_service=price_service
    )
    analytics_service = AnalyticsService(repo=repo)
    alert_service = AlertService(
        price_service=price_service, notifier=notifier,
        repo=repo, trade_service=trade_service
    )

    return {
        "trade_service": trade_service,
        "analytics_service": analytics_service,
        "price_service": price_service,
        "alert_service": alert_service,
        "notifier": notifier,
        "market_data_service": market_data_service,
    }

def bootstrap_app() -> Optional[Application]:
    """
    Centralized application bootstrapping function.
    This creates the bot, builds the services, and injects them correctly.
    This is now the SINGLE SOURCE OF TRUTH for creating a fully configured bot application.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        return None
        
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    
    # Build services and inject the application context into them
    services = build_services(ptb_app)
    
    # Inject the fully built services into the bot's shared context data
    ptb_app.bot_data["services"] = services
    
    # Register all handlers that will use these services. The order is critical.
    register_all_handlers(ptb_app)
    
    return ptb_app

# --- END OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.0) ---