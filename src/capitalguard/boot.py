# src/capitalguard/boot.py (Fixed Import)
"""
Bootstrap - إصلاح مشاكل الاستيراد
"""

import logging
from telegram.ext import Application

from capitalguard.config import settings
from capitalguard.infrastructure.notify.telegram import TelegramNotifier

# ✅ الإصلاح: استيراد مباشر من الملفات بدلاً من الحزمة
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.market_data_service import MarketDataService

from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.interfaces.telegram.handlers import register_all_handlers

logger = logging.getLogger(__name__)

def bootstrap_app() -> Application:
    """إعداد التطبيق مع إصلاح الاستيراد"""
    
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not configured")
        return None

    try:
        # إنشاء تطبيق Telegram
        ptb_app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .build()
        )

        # إعداد الخدمات
        notifier = TelegramNotifier()
        notifier.set_ptb_app(ptb_app)
        
        repo = RecommendationRepository()
        market_data_service = MarketDataService()
        price_service = PriceService()
        
        # إنشاء TradeService أولاً
        trade_service = TradeService(
            repo=repo,
            notifier=notifier,
            market_data_service=market_data_service,
            price_service=price_service,
            alert_service=None  # سيتم تعيينه لاحقاً
        )
        
        analytics_service = AnalyticsService(repo=repo)
        
        # إنشاء AlertService
        alert_service = AlertService(
            trade_service=trade_service,
            repo=repo,
            notifier=notifier,
            admin_chat_id=settings.TELEGRAM_ADMIN_CHAT_ID,
            main_loop=ptb_app.running_loop,
        )
        
        # ربط الخدمات ببعضها
        trade_service.alert_service = alert_service

        # تخزين الخدمات في bot_data
        ptb_app.bot_data.update({
            "trade_service": trade_service,
            "analytics_service": analytics_service,
            "price_service": price_service,
            "alert_service": alert_service,
            "notifier": notifier,
            "market_data_service": market_data_service,
        })

        # تسجيل handlers
        register_all_handlers(ptb_app)

        logger.info("✅ Application bootstrapped successfully")
        return ptb_app

    except Exception as e:
        logger.error("❌ Failed to bootstrap application: %s", e)
        return None

def build_services():
    """بناء الخدمات للاستخدام عندما لا يكون البوت متاحاً"""
    return {
        "trade_service": None,
        "analytics_service": None,
        "price_service": None,
        "alert_service": None,
        "notifier": None,
        "market_data_service": None,
    }

async def initialize_services(ptb_app: Application):
    """تهيئة الخدمات بشكل غير متزامن"""
    if not ptb_app:
        return
        
    try:
        services = ptb_app.bot_data
        alert_service = services.get("alert_service")
        market_data_service = services.get("market_data_service")
        
        logger.info("🔄 Starting async service initialization...")
        
        # 1. تحديث Market Data Cache
        if market_data_service:
            logger.info("📊 Refreshing market data cache...")
            try:
                await market_data_service.refresh_symbols_cache()
                logger.info("✅ Market data cache refreshed")
            except Exception as e:
                logger.error("❌ Market data cache refresh failed: %s", e)
        
        # 2. بناء فهرس المحفزات
        if alert_service:
            logger.info("🔍 Building triggers index...")
            try:
                await alert_service.build_triggers_index()
                logger.info("✅ Triggers index built")
            except Exception as e:
                logger.error("❌ Triggers index build failed: %s", e)
        
        # 3. بدء AlertService
        if alert_service:
            alert_service.start()
            logger.info("✅ AlertService started")
            
        logger.info("🎉 All services initialized")
        
    except Exception as e:
        logger.error("❌ Service initialization failed: %s", e)