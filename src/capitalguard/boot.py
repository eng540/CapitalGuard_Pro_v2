# --- src/capitalguard/boot.py ---
# src/capitalguard/boot.py (v26.4 - Final Dependency Wiring)
"""
Bootstrap and dependency injection setup for the application.
✅ Final version - fully wired, dependency-safe, and production-ready.
- Unified repository handling.
- Circular dependency between TradeService and AlertService resolved.
- Compatible with TradeService v31.0.8.
"""

import os
import logging
from typing import Dict, Any, Optional

from telegram.ext import Application, BasePersistence

from capitalguard.config import settings
from capitalguard.application.services import (
    TradeService, AnalyticsService, PriceService, AlertService,
    MarketDataService, AuditService
)
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.application.strategy.engine import StrategyEngine
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, UserRepository, ChannelRepository,
    ParsingRepository
)
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

log = logging.getLogger(__name__)

# ---------------------------------------------------------
# Service Builder
# ---------------------------------------------------------
def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """Build and wire all application services and dependencies."""
    log.info("Building application services...")
    services: Dict[str, Any] = {}

    try:
        # --- Telegram Notifier ---
        notifier = TelegramNotifier()
        if ptb_app:
            notifier.set_ptb_app(ptb_app)
        services["notifier"] = notifier

        # --- Repository Class References (not instances) ---
        services["recommendation_repo_class"] = RecommendationRepository
        services["user_repo_class"] = UserRepository
        services["channel_repo_class"] = ChannelRepository
        services["parsing_repo_class"] = ParsingRepository

        # --- Core Stateless Services ---
        services["price_service"] = PriceService()
        services["market_data_service"] = MarketDataService()

        # --- Repo-based Services ---
        services["analytics_service"] = AnalyticsService(repo_class=RecommendationRepository)
        services["audit_service"] = AuditService(
            rec_repo_class=RecommendationRepository,
            user_repo_class=UserRepository
        )

        # --- Parsing Service ---
        services["parsing_service"] = ParsingService(
            parsing_repo_class=ParsingRepository
        )

        # --- Trading Core ---
        trade_service = TradeService(
            repo=RecommendationRepository(),
            notifier=notifier,
            market_data_service=services["market_data_service"],
            price_service=services["price_service"]
        )
        services["trade_service"] = trade_service

        # --- Strategy Engine ---
        strategy_engine = StrategyEngine(trade_service=trade_service)
        services["strategy_engine"] = strategy_engine

        # --- Alert Service ---
        alert_service = AlertService(
            trade_service=trade_service,
            price_service=services["price_service"],
            repo=RecommendationRepository(),
            strategy_engine=strategy_engine
        )
        services["alert_service"] = alert_service

        # --- Final Dependency Injection ---
        trade_service.alert_service = alert_service

        log.info("✅ All services built and wired successfully.")
        return services

    except Exception as e:
        log.critical(f"❌ Service building failed: {e}", exc_info=True)
        raise

# ---------------------------------------------------------
# Application Bootstrap
# ---------------------------------------------------------
def bootstrap_app(persistence: Optional[BasePersistence] = None) -> Optional[Application]:
    """
    Bootstraps the Telegram Application instance.
    Ensures that TELEGRAM_BOT_TOKEN and persistence are initialized.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set. Bot cannot start.")
        return None

    try:
        if persistence is None:
            from telegram.ext import PicklePersistence
            log.warning("No persistence object provided. Using default PicklePersistence.")
            persistence = PicklePersistence(filepath="./telegram_bot_persistence")

        ptb_app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .persistence(persistence)
            .build()
        )

        log.info("✅ Telegram Application built successfully.")
        return ptb_app

    except Exception as e:
        log.critical(f"❌ Application bootstrap failed during PTB app creation: {e}", exc_info=True)
        raise

# --- END of boot.py ---