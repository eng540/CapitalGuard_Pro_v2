# File: src/capitalguard/boot.py
# Version: v3.1.0-R2 (Service Wiring)
# ✅ THE FIX: (R2 Architecture - Wiring)
#    - 1. (DI) حقن `CreationService` و `LifecycleService` في `TradeService` (الواجهة).
#    - 2. (DI) حقن `LifecycleService` (الجديدة) في `AlertService` و `StrategyEngine`
#       بدلاً من `TradeService` القديمة لإدارة الأحداث.
#    - 3. (DI) حقن `AlertService` في الخدمات الجديدة (`CreationService`, `LifecycleService`)
#       للسماح بالفهرسة الذكية (Smart Indexing).
# 🎯 IMPACT: النظام الآن موصول (wired) بالكامل وفقًا للمعمارية الجديدة (SoC).

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
    ImageParsingService,
    PerformanceService,
    CreationService,
    LifecycleService,
)
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.application.strategy.engine import StrategyEngine
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository,
    UserRepository,
    ChannelRepository,
    ParsingRepository,
    PerformanceRepository
)
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds

log = logging.getLogger(__name__)


def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """Build and wire all application services and dependencies."""
    log.info("Building application services (R2 Wiring)...")
    services: Dict[str, Any] = {}

    try:
        notifier = TelegramNotifier()
        if ptb_app:
            notifier.set_ptb_app(ptb_app)
        services["notifier"] = notifier

        recommendation_repo_instance = RecommendationRepository()

        # --- Repository Classes (for UoW) ---
        services["recommendation_repo_class"] = RecommendationRepository
        services["user_repo_class"] = UserRepository
        services["channel_repo_class"] = ChannelRepository
        services["parsing_repo_class"] = ParsingRepository
        services["performance_repo_class"] = PerformanceRepository

        # --- Core Services (Instances) ---
        services["price_service"] = PriceService()
        services["market_data_service"] = MarketDataService()
        services["analytics_service"] = AnalyticsService(repo=recommendation_repo_instance)
        services["performance_service"] = PerformanceService(repo_class=PerformanceRepository)
        services["audit_service"] = AuditService(rec_repo=recommendation_repo_instance, user_repo_class=UserRepository)
        services["parsing_service"] = ParsingService(parsing_repo_class=ParsingRepository)
        services["image_parsing_service"] = ImageParsingService()

        # --- R2 Service Instantiation ---
        
        # 1. إنشاء الخدمات المستقلة الجديدة
        creation_service = CreationService(
            repo=recommendation_repo_instance,
            notifier=notifier,
            market_data_service=services["market_data_service"],
            price_service=services["price_service"],
        )
        lifecycle_service = LifecycleService(
            repo=recommendation_repo_instance,
            notifier=notifier,
        )
        
        # 2. إنشاء الواجهة (Facade) وحقن الخدمات الجديدة فيها
        trade_service_facade = TradeService(
            repo=recommendation_repo_instance,
            notifier=notifier,
            market_data_service=services["market_data_service"],
            price_service=services["price_service"],
            # ✅ DI: حقن الخدمات المتخصصة في الواجهة
            creation_service=creation_service,
            lifecycle_service=lifecycle_service
        )

        # 3. إنشاء خدمات الاستراتيجية والتنبيه
        # ✅ DI: حقن LifecycleService (بدلاً من TradeService)
        strategy_engine = StrategyEngine(lifecycle_service=lifecycle_service)
        
        alert_service = AlertService(
            lifecycle_service=lifecycle_service, # ✅ DI: استخدام الخدمة الجديدة
            price_service=services["price_service"],
            repo=recommendation_repo_instance,
            strategy_engine=strategy_engine,
        )

        # 4. حقن الاعتماديات الدائرية (Circular DI)
        # ✅ DI: حقن AlertService في الخدمات الجديدة
        trade_service_facade.alert_service = alert_service
        creation_service.alert_service = alert_service
        creation_service.lifecycle_service = lifecycle_service # (إذا احتاجت Creation استدعاء Lifecycle)
        lifecycle_service.alert_service = alert_service
        
        # ✅ DI: حقن LifecycleService في StrategyEngine
        strategy_engine.lifecycle_service = lifecycle_service

        # 5. تسجيل الخدمات في الحاوية (Container)
        services["trade_service"] = trade_service_facade
        services["creation_service"] = creation_service
        services["lifecycle_service"] = lifecycle_service
        services["strategy_engine"] = strategy_engine
        services["alert_service"] = alert_service

        log.info("✅ All services built and wired successfully (R2 Architecture).")
        return services

    except Exception as e:
        log.critical(f"❌ Service building failed: {e}", exc_info=True)
        raise

def bootstrap_app(persistence: Optional[BasePersistence] = None) -> Optional[Application]:
    """
    Bootstraps the Telegram Application instance.
    (This function remains unchanged)
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