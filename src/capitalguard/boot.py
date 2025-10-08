# src/capitalguard/boot.py (v25.2 - FINAL & DECOUPLED)
"""
Bootstrap and dependency injection setup for the application.
This version decouples the boot process from the handler registration.
"""

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
from capitalguard.application.services.audit_service import AuditService
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository, ChannelRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

log = logging.getLogger(__name__)

def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """Builds and wires all application services."""
    log.info("Building application services...")
    services = {}
    
    try:
        notifier = TelegramNotifier()
        if ptb_app:
            notifier.set_ptb_app(ptb_app)
        services['notifier'] = notifier

        if not os.getenv("BINANCE_API_KEY"):
            log.warning("⚠️ Binance credentials not found - auto trading disabled")
        
        services['recommendation_repo'] = RecommendationRepository()
        services['user_repo_class'] = UserRepository
        services['channel_repo_class'] = ChannelRepository

        services['price_service'] = PriceService()
        services['market_data_service'] = MarketDataService()
        services['analytics_service'] = AnalyticsService(repo=services['recommendation_repo'])
        services['audit_service'] = AuditService(rec_repo=services['recommendation_repo'], user_repo_class=services['user_repo_class'])
        
        trade_service = TradeService(
            repo=services['recommendation_repo'],
            notifier=services['notifier'],
            market_data_service=services['market_data_service'],
            price_service=services['price_service']
        )
        services['trade_service'] = trade_service
        
        alert_service = AlertService(
            trade_service=trade_service,
            price_service=services['price_service'],
            repo=services['recommendation_repo']
        )
        services['alert_service'] = alert_service
        
        trade_service.alert_service = alert_service
        
        log.info("✅ All services built successfully.")
        return services

    except Exception as e:
        log.critical(f"❌ Service building failed: {e}", exc_info=True)
        raise

def bootstrap_app() -> Optional[Application]:
    """Bootstraps the Telegram Application instance, but does NOT register handlers."""
    if not settings.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set. Bot cannot start.")
        return None
        
    try:
        persistence = PicklePersistence(filepath="./telegram_bot_persistence")
        ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
        return ptb_app
    except Exception as e:
        log.critical(f"❌ Application bootstrap failed during PTB app creation: {e}", exc_info=True)
        raise

#END