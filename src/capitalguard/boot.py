# src/capitalguard/boot.py (v26.5 - DI Hotfix)

"""
Bootstrap and dependency injection setup for the application.
✅ FIX: Corrected DI wiring for AnalyticsService and AuditService.

- AnalyticsService now receives an instance of RecommendationRepository.
- AuditService now receives an instance of RecommendationRepository and the UserRepository class.
- This resolves the DI Mismatch errors identified during startup analysis.
"""

import logging
from typing import Dict, Any, Optional
from telegram.ext import Application, BasePersistence

from capitalguard.config import settings
from capitalguard.application.services import (
    TradeService,
    AnalyticsService,
    PriceService,
    AlertService,
    MarketDataService,
    AuditService,
)
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.application.strategy.engine import StrategyEngine
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository,
    UserRepository,
    ChannelRepository,
    ParsingRepository,
)
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

log = logging.getLogger(__name__)


def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """Build and wire all application services and dependencies."""
    log.info("Building application services...")
    services: Dict[str, Any] = {}

    try:
        notifier = TelegramNotifier()
        if ptb_app:
            notifier.set_ptb_app(ptb_app)
        services["notifier"] = notifier

        recommendation_repo_instance = RecommendationRepository()

        services["recommendation_repo_class"] = RecommendationRepository
        services["user_repo_class"] = UserRepository
        services["channel_repo_class"] = ChannelRepository
        services["parsing_repo_class"] = ParsingRepository

        services["price_service"] = PriceService()
        services["market_data_service"] = MarketDataService()

        services["analytics_service"] = AnalyticsService(repo=recommendation_repo_instance)
        services["audit_service"] = AuditService(
            rec_repo=recommendation_repo_instance,
            user_repo_class=UserRepository,
        )
        services["parsing_service"] = ParsingService(parsing_repo_class=ParsingRepository)

        trade_service = TradeService(
            repo=recommendation_repo_instance,
            notifier=notifier,
            market_data_service=services["market_data_service"],
            price_service=services["price_service"],
        )
        services["trade_service"] = trade_service

        strategy_engine = StrategyEngine(trade_service=trade_service)
        services["strategy_engine"] = strategy_engine

        alert_service = AlertService(
            trade_service=trade_service,
            price_service=services["price_service"],
            repo=recommendation_repo_instance,
            strategy_engine=strategy_engine,
        )
        services["alert_service"] = alert_service

        trade_service.alert_service = alert_service

        log.info("✅ All services built and wired successfully.")
        return services

    except Exception as e:
        log.critical(f"❌ Service building failed: {e}", exc_info=True)
        raise


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
        log.critical(f"❌ Application bootstrap failed: {e}", exc_info=True)
        raise