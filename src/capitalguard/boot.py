# src/capitalguard/boot.py (v26.2 - StrategyEngine DI)
"""
Bootstrap and dependency injection setup for the application.
✅ NEW: Initializes and injects the new StrategyEngine into the AlertService.
This file is responsible for assembling all application components.
"""

import os
import logging
from typing import Dict, Any, Optional

from telegram.ext import Application, BasePersistence

from capitalguard.config import settings
from capitalguard.application.services import (
    TradeService, AnalyticsService, PriceService, AlertService,
    MarketDataService, AuditService, ImageParsingService
)
from capitalguard.application.strategy.engine import StrategyEngine
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository, ChannelRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier

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
        
        services['recommendation_repo'] = RecommendationRepository()
        services['user_repo_class'] = UserRepository
        services['channel_repo_class'] = ChannelRepository

        # --- Core Application Services ---
        services['price_service'] = PriceService()
        services['market_data_service'] = MarketDataService()
        services['analytics_service'] = AnalyticsService(repo=services['recommendation_repo'])
        services['audit_service'] = AuditService(rec_repo=services['recommendation_repo'], user_repo_class=services['user_repo_class'])
        services['image_parsing_service'] = ImageParsingService()
        
        trade_service = TradeService(
            repo=services['recommendation_repo'],
            notifier=services['notifier'],
            market_data_service=services['market_data_service'],
            price_service=services['price_service']
        )
        services['trade_service'] = trade_service
        
        # --- Strategy & Alerting Layer ---
        
        # 1. Initialize the new StrategyEngine
        strategy_engine = StrategyEngine(trade_service=trade_service)
        services['strategy_engine'] = strategy_engine
        
        # 2. Initialize AlertService and inject the StrategyEngine
        alert_service = AlertService(
            trade_service=trade_service,
            price_service=services['price_service'],
            repo=services['recommendation_repo'],
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
    """Bootstraps the Telegram Application instance."""
    if not settings.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set. Bot cannot start.")
        return None
    try:
        if persistence is None:
            from telegram.ext import PicklePersistence
            log.warning("No persistence object provided. Falling back to default PicklePersistence.")
            persistence = PicklePersistence(filepath="./telegram_bot_persistence")
            
        ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
        return ptb_app
    except Exception as e:
        log.critical(f"❌ Application bootstrap failed during PTB app creation: {e}", exc_info=True)
        raise