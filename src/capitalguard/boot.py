# src/capitalguard/boot.py (Version 3.0.0 - Enhanced Monitoring)
"""
Bootstrap - إعداد النظام مع المراقبة المحسنة وإصلاح الفشل الصامت
"""

import logging
import asyncio
from telegram.ext import Application

from capitalguard.config import settings
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services import (
    TradeService, AnalyticsService, PriceService, 
    AlertService, MarketDataService
)
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.monitoring.system_monitor import SystemMonitor
from capitalguard.interfaces.telegram.handlers import register_all_handlers
from capitalguard.service_registry import ServiceRegistry

logger = logging.getLogger(__name__)

def bootstrap_app() -> Application:
    """إعداد التطبيق مع التحسينات الشاملة"""
    
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not configured - Bot features disabled")
        return None

    try:
        # إنشاء تطبيق Telegram
        ptb_app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .build()
        )

        logger.info("🔄 Initializing services...")

        # إعداد الخدمات الأساسية
        notifier = TelegramNotifier()
        notifier.set_ptb_app(ptb_app)
        
        repo = RecommendationRepository()
        market_data_service = MarketDataService()
        price_service = PriceService()
        
        # إنشاء TradeService أولاً (بدون alert_service مؤقتاً)
        trade_service = TradeService(
            repo=repo,
            notifier=notifier,
            market_data_service=market_data_service,
            price_service=price_service,
            alert_service=None  # سيتم تعيينه بعد الإنشاء
        )
        
        analytics_service = AnalyticsService(repo=repo)
        
        # إنشاء AlertService مع تحسينات الموثوقية
        alert_service = AlertService(
            trade_service=trade_service,
            repo=repo,
            notifier=notifier,
            admin_chat_id=settings.TELEGRAM_ADMIN_CHAT_ID,
            main_loop=ptb_app.running_loop,
        )
        
        # ربط الخدمات ببعضها
        trade_service.alert_service = alert_service

        # إنشاء System Monitor
        system_monitor = SystemMonitor(alert_service=alert_service)

        # تخزين الخدمات في bot_data للوصول السريع
        services_dict = {
            "trade_service": trade_service,
            "analytics_service": analytics_service,
            "price_service": price_service,
            "alert_service": alert_service,
            "notifier": notifier,
            "market_data_service": market_data_service,
            "system_monitor": system_monitor,
        }
        
        ptb_app.bot_data.update(services_dict)

        # تسجيل الخدمات في السجل العالمي
        registry = ServiceRegistry()
        for name, service in services_dict.items():
            registry.register(name, service)
        
        # تسجيل handlers
        register_all_handlers(ptb_app)

        logger.info("✅ Application bootstrapped successfully with %d services", len(services_dict))
        return ptb_app

    except Exception as e:
        logger.error("💥 CRITICAL: Failed to bootstrap application: %s", e, exc_info=True)
        return None

async def initialize_services(ptb_app: Application):
    """تهيئة الخدمات بشكل غير متزامن بعد بدء التطبيق"""
    if not ptb_app:
        return
        
    try:
        services = ptb_app.bot_data
        alert_service = services.get("alert_service")
        market_data_service = services.get("market_data_service")
        system_monitor = services.get("system_monitor")
        
        logger.info("🔄 Starting async service initialization...")
        
        # 1. تحديث Market Data Cache
        if market_data_service:
            logger.info("📊 Refreshing market data cache...")
            try:
                await market_data_service.refresh_symbols_cache()
                logger.info("✅ Market data cache refreshed successfully")
            except Exception as e:
                logger.error("❌ Market data cache refresh failed: %s", e)
        
        # 2. بناء فهرس المحفزات
        if alert_service:
            logger.info("🔍 Building triggers index...")
            try:
                await alert_service.build_triggers_index()
                logger.info("✅ Triggers index built successfully")
            except Exception as e:
                logger.error("❌ Triggers index build failed: %s", e)
        
        # 3. بدء System Monitor
        if system_monitor:
            system_monitor.start()
            logger.info("✅ System monitor started")
        
        # 4. بدء AlertService
        if alert_service:
            alert_service.start()
            logger.info("✅ AlertService started")
            
        logger.info("🎉 All services initialized successfully")
        
    except Exception as e:
        logger.error("💥 Service initialization failed: %s", e, exc_info=True)