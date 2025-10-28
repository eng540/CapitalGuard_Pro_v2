# --- src/capitalguard/boot.py ---
# src/capitalguard/boot.py (v26.3 - Parsing Service Integration)
"""
Bootstrap and dependency injection setup for the application.
✅ Includes initialization for ParsingRepository and ParsingService.
"""

import os
import logging
from typing import Dict, Any, Optional

from telegram.ext import Application, BasePersistence

from capitalguard.config import settings
from capitalguard.application.services import (
    TradeService, AnalyticsService, PriceService, AlertService,
    MarketDataService, AuditService # Removed ImageParsingService import
)
# ✅ NEW: Import the refactored ParsingService
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.application.strategy.engine import StrategyEngine
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, UserRepository, ChannelRepository,
    # ✅ NEW: Import ParsingRepository
    ParsingRepository
)
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
# Import BinanceExec if needed for other services, although not directly used here.
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

log = logging.getLogger(__name__)

def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """Builds and wires all application services."""
    log.info("Building application services...")
    services = {}

    try:
        # --- Infrastructure Components ---
        notifier = TelegramNotifier()
        if ptb_app:
            notifier.set_ptb_app(ptb_app)
        services['notifier'] = notifier

        # Store repository *classes* for UOW pattern elsewhere
        services['recommendation_repo_class'] = RecommendationRepository
        services['user_repo_class'] = UserRepository
        services['channel_repo_class'] = ChannelRepository
        # ✅ NEW: Initialize ParsingRepository class type
        services['parsing_repo_class'] = ParsingRepository

        # --- Core Application Services ---
        services['price_service'] = PriceService()
        services['market_data_service'] = MarketDataService()

        # Services needing repo instances (will get session via UOW)
        # Pass the repo *class* to AnalyticsService and AuditService constructors
        services['analytics_service'] = AnalyticsService(repo=RecommendationRepository()) # Instantiating repo here might be less ideal than passing class
        services['audit_service'] = AuditService(rec_repo=RecommendationRepository(), user_repo_class=services['user_repo_class'])

        # ✅ NEW: Initialize the new ParsingService
        # ParsingService now takes the repo *class*
        services['parsing_service'] = ParsingService(
             parsing_repo_class=services['parsing_repo_class']
             # Add spaCy model path or other dependencies if needed later
        )

        trade_service = TradeService(
            # Pass repo *instance* - TradeService likely manages sessions internally or expects them passed in methods
            repo=RecommendationRepository(), # Again, consider passing class or refactoring TradeService
            notifier=services['notifier'],
            market_data_service=services['market_data_service'],
            price_service=services['price_service']
        )
        services['trade_service'] = trade_service

        # --- Strategy & Alerting Layer ---
        strategy_engine = StrategyEngine(trade_service=trade_service)
        services['strategy_engine'] = strategy_engine

        alert_service = AlertService(
            trade_service=trade_service,
            price_service=services['price_service'],
            # AlertService needs repo instance to build index frequently
            repo=RecommendationRepository(), # Consider session management here too
            strategy_engine=strategy_engine
        )
        services['alert_service'] = alert_service

        # Circular dependency injection
        trade_service.alert_service = alert_service

        log.info("✅ All services built and wired successfully.")
        return services

    except Exception as e:
        log.critical(f"❌ Service building failed: {e}", exc_info=True)
        raise

def bootstrap_app(persistence: Optional[BasePersistence] = None) -> Optional[Application]:
    """Bootstraps the Telegram Application instance, accepting persistence object."""
    if not settings.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set. Bot cannot start.")
        return None
    try:
        if persistence is None:
            # Fallback for local testing or if no persistence is passed
            from telegram.ext import PicklePersistence
            log.warning("No persistence object provided. Falling back to default PicklePersistence.")
            persistence = PicklePersistence(filepath="./telegram_bot_persistence")

        ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
        return ptb_app
    except Exception as e:
        log.critical(f"❌ Application bootstrap failed during PTB app creation: {e}", exc_info=True)
        raise

# --- END of boot update ---