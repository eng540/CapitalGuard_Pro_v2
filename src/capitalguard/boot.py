# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/boot.py --- v6
"""
src/capitalguard/boot.py — R3 Architecture
DI-Oriented Boot Layer compatible with:
- StrategyEngine v4.0
- AlertService v28-R3
- R3 Unified Services Architecture
"""

import logging
from typing import Dict, Any, Optional
from telegram.ext import Application, BasePersistence

from capitalguard.config import settings

# --- Core Service Imports (R3 DI Architecture) ---
from capitalguard.application.services import (
    TradeService,
    AnalyticsService,
    PriceService,
    AlertService,
    MarketDataService,
    AuditService,
    ImageParsingService,
    PerformanceService,
    CreationService,
    LifecycleService,
)
from capitalguard.application.services.parsing_service import ParsingService

# R3 Strategy engine v4.0
from capitalguard.application.strategy.engine import StrategyEngine

# Repository Layer
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository,
    UserRepository,
    ChannelRepository,
    ParsingRepository,
)
from capitalguard.infrastructure.db.performance_repository import PerformanceRepository

# Notifiers, Executors
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

log = logging.getLogger(__name__)


def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """
    R3 — Full Dependency Tree Construction
    Unified DI Container:
      - Repositories
      - Core Services
      - Strategy Engine v4
      - AlertService (Action Executor)
      - Facade Services
    """
    log.info("Building services (R3 Architecture)...")

    services: Dict[str, Any] = {}

    try:
        # --- Notifier ---
        notifier = TelegramNotifier()
        if ptb_app:
            notifier.set_ptb_app(ptb_app)
        services["notifier"] = notifier

        # --- Repositories (singleton instances) ---
        recommendation_repo = RecommendationRepository()
        services["recommendation_repo"] = recommendation_repo
        services["recommendation_repo_class"] = RecommendationRepository
        services["user_repo_class"] = UserRepository
        services["channel_repo_class"] = ChannelRepository
        services["parsing_repo_class"] = ParsingRepository
        services["performance_repo_class"] = PerformanceRepository

        # --- Core Non-DI Services ---
        services["price_service"] = PriceService()
        services["market_data_service"] = MarketDataService()
        services["analytics_service"] = AnalyticsService(repo=recommendation_repo)
        services["performance_service"] = PerformanceService(repo_class=PerformanceRepository)
        services["audit_service"] = AuditService(
            rec_repo=recommendation_repo,
            user_repo_class=UserRepository
        )
        services["parsing_service"] = ParsingService(parsing_repo_class=ParsingRepository)
        services["image_parsing_service"] = ImageParsingService()

        # --- R3 Specialized Services ---
        creation_service = CreationService(
            repo=recommendation_repo,
            notifier=notifier,
            market_data_service=services["market_data_service"],
            price_service=services["price_service"],
        )

        lifecycle_service = LifecycleService(
            repo=recommendation_repo,
            notifier=notifier,
        )

        # --- Strategy Engine v4 ---
        strategy_engine = StrategyEngine(
            lifecycle_service=lifecycle_service,
            storage=None,
            metrics=None,
            config={"percentage_threshold": 10, "min_sl_move": "0"}
        )

        # --- AlertService (Action Executor) ---
        alert_service = AlertService(
            lifecycle_service=lifecycle_service,
            price_service=services["price_service"],
            repo=recommendation_repo,
            strategy_engine=strategy_engine,
        )

        # --- Trade Facade (wraps creation + lifecycle) ---
        trade_service_facade = TradeService(
            repo=recommendation_repo,
            notifier=notifier,
            market_data_service=services["market_data_service"],
            price_service=services["price_service"],
            creation_service=creation_service,
            lifecycle_service=lifecycle_service
        )

        # --- Circular DI (R3 Safe Wiring) ---
        creation_service.alert_service = alert_service
        creation_service.lifecycle_service = lifecycle_service

        lifecycle_service.alert_service = alert_service

        trade_service_facade.alert_service = alert_service

        strategy_engine.lifecycle_service = lifecycle_service

        # --- Register all in container ---
        services["trade_service"] = trade_service_facade
        services["creation_service"] = creation_service
        services["lifecycle_service"] = lifecycle_service
        services["strategy_engine"] = strategy_engine
        services["alert_service"] = alert_service

        log.info("Services built successfully (R3 Architecture).")
        return services

    except Exception as e:
        log.critical("Service building failed: %s", e, exc_info=True)
        raise


def bootstrap_app(persistence: Optional[BasePersistence] = None) -> Optional[Application]:
    """
    Telegram Application Bootstrapper (unchanged)
    Responsible for initiating PTB App.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set.")
        return None

    try:
        if persistence is None:
            from telegram.ext import PicklePersistence
            persistence = PicklePersistence(filepath="./telegram_bot_persistence")

        ptb_app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .persistence(persistence)
            .build()
        )

        log.info("Telegram Application built.")
        return ptb_app

    except Exception as e:
        log.critical("Application bootstrap failed: %s", e, exc_info=True)
        raise
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/boot.py ---