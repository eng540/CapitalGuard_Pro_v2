# src/capitalguard/boot.py (Fixed - Version 3.2.0)
"""
Bootstrap - إصلاح مشكلة KeyError: 'services'
"""

import logging
import asyncio
from telegram.ext import Application

from capitalguard.config import settings
from capitalguard.infrastructure.notify.telegram import TelegramNotifier

# استيراد مباشر من الملفات
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.market_data_service import MarketDataService

from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.interfaces.telegram.handlers import register_all_handlers

logger = logging.getLogger(__name__)

def bootstrap_app() -> Application:
    """إعداد التطبيق مع إصلاح إضافة services"""
    
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

        # ✅ تهيئة bot_data إذا كان فارغاً
        if not hasattr(ptb_app, 'bot_data') or ptb_app.bot_data is None:
            ptb_app.bot_data = {}

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
        
        # استخدام asyncio.get_event_loop()
        main_loop = asyncio.get_event_loop()
        
        # إنشاء AlertService
        alert_service = AlertService(
            trade_service=trade_service,
            repo=repo,
            notifier=notifier,
            admin_chat_id=settings.TELEGRAM_ADMIN_CHAT_ID,
            main_loop=main_loop,
        )
        
        # ربط الخدمات ببعضها
        trade_service.alert_service = alert_service

        # ✅ الإصلاح الحرج: إنشاء قاموس services وإضافته إلى bot_data
        services_dict = {
            "trade_service": trade_service,
            "analytics_service": analytics_service,
            "price_service": price_service,
            "alert_service": alert_service,
            "notifier": notifier,
            "market_data_service": market_data_service,
        }
        
        # ✅ إضافة المفتاح "services" إلى bot_data
        ptb_app.bot_data["services"] = services_dict
        
        logger.info("✅ Services dictionary created and added to bot_data")

        # تسجيل handlers
        register_all_handlers(ptb_app)

        logger.info("✅ Application bootstrapped successfully with services")
        return ptb_app

    except Exception as e:
        logger.error("❌ Failed to bootstrap application: %s", e, exc_info=True)
        return None

def build_services():
    """بناء الخدمات للاستخدام عندما لا يكون البوت متاحاً"""
    logger.warning("⚠️ Building fallback services (Telegram Bot not available)")
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
        logger.warning("⚠️ Cannot initialize services: ptb_app is None")
        return
        
    try:
        # ✅ التحقق من وجود المفتاح "services"
        if "services" not in ptb_app.bot_data:
            logger.error("❌ Key 'services' not found in bot_data")
            return
            
        services = ptb_app.bot_data["services"]
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
        else:
            logger.warning("⚠️ Market data service not available")
        
        # 2. بناء فهرس المحفزات
        if alert_service:
            logger.info("🔍 Building triggers index...")
            try:
                await alert_service.build_triggers_index()
                logger.info("✅ Triggers index built")
            except Exception as e:
                logger.error("❌ Triggers index build failed: %s", e)
        else:
            logger.warning("⚠️ Alert service not available")
        
        # 3. بدء AlertService
        if alert_service:
            alert_service.start()
            logger.info("✅ AlertService started")
        else:
            logger.warning("⚠️ Cannot start AlertService: not available")
            
        logger.info("🎉 Service initialization completed")
        
    except Exception as e:
        logger.error("❌ Service initialization failed: %s", e, exc_info=True)