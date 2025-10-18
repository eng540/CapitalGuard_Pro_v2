# src/capitalguard/boot.py
# --- START OF PRODUCTION-READY BOOTSTRAP FILE ---
"""
Bootstrap and dependency injection setup for the application.

✅ THE FIX: bootstrap accepts an external `persistence` (e.g., RedisPersistence)
and safely wires the PTB Application, notifier and services.
"""
import os
import logging
from typing import Dict, Any, Optional

from telegram.ext import Application, PicklePersistence, BasePersistence

from capitalguard.config import settings
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.audit_service import AuditService
from capitalguard.application.services.image_parsing_service import ImageParsingService
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository, ChannelRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

log = logging.getLogger(__name__)

def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    log.info("Building application services...")
    services: Dict[str, Any] = {}
    try:
        notifier = TelegramNotifier()
        if ptb_app:
            notifier.set_ptb_app(ptb_app)
        services['notifier'] = notifier

        services['recommendation_repo'] = RecommendationRepository()
        services['user_repo_class'] = UserRepository
        services['channel_repo_class'] = ChannelRepository

        # price & market services
        services['price_service'] = PriceService()
        services['market_data_service'] = MarketDataService()
        services['image_parsing_service'] = ImageParsingService()
        services['audit_service'] = AuditService(services['recommendation_repo'], UserRepository)

        # trade service wiring
        services['trade_service'] = TradeService(
            repo=services['recommendation_repo'],
            notifier=notifier,
            market_data_service=services['market_data_service'],
            price_service=services['price_service'],
        )

        # alert service (optional)
        services['alert_service'] = AlertService(services['market_data_service'], services['recommendation_repo'])

        log.info("Services built successfully.")
        return services
    except Exception as e:
        log.exception("Failed to build services: %s", e)
        raise

def bootstrap_app(persistence: Optional[BasePersistence] = None) -> Application:
    """
    Create and return a configured telegram.ext.Application instance.
    If persistence is provided (RedisPersistence, PicklePersistence), use it.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        log.critical("TELEGRAM_BOT_TOKEN not set.")
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    builder = Application.builder().token(bot_token)

    if persistence:
        builder = builder.persistence(persistence)
    else:
        # local (non-persistent) fallback - pickled ephemeral state if desired
        builder = builder.persistence(PicklePersistence(filename="ptb_persistence.pkl"))

    app = builder.build()
    # attach services and placeholder for later initialization
    app.bot_data["services"] = build_services(ptb_app=app)
    log.info("Telegram Application bootstrapped.")

    return app
# --- END OF FILE ---