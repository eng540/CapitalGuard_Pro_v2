# src/capitalguard/boot.py (الإصدار المصحح)
"""
Bootstrap and dependency injection setup for CapitalGuard Pro.
Production-ready version - FIXED
"""

import os
import logging
from typing import Dict, Any, Optional

from telegram.ext import Application
from sqlalchemy import create_engine, text  # ✅ أضف text هنا
from sqlalchemy.orm import sessionmaker

from capitalguard.config import settings
from capitalguard.infrastructure.db.models import Base
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, 
    UserRepository, 
    ChannelRepository
)
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer
from capitalguard.infrastructure.sched.shared_queue import ThreadSafeQueue

# Service Imports
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.autotrade_service import AutoTradeService
from capitalguard.application.services.risk_service import RiskService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.audit_service import AuditService
from capitalguard.application.services.image_parsing_service import ImageParsingService

log = logging.getLogger(__name__)

def setup_database():
    """إعداد قاعدة البيانات للإنتاج"""
    try:
        engine = create_engine(
            settings.DATABASE_URL,
            connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
            echo=False,
            pool_pre_ping=True  # ✅ مهم للإنتاج
        )
        
        # التحقق من اتصال قاعدة البيانات
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))  # ✅ استخدم text() هنا
        
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        log.info("✅ Database setup completed successfully")
        return SessionLocal
        
    except Exception as e:
        log.critical(f"❌ Database setup failed: {e}")
        raise

def build_services() -> Dict[str, Any]:
    """بناء جميع الخدمات للإنتاج"""
    services = {}
    
    try:
        # ✅ FIX: إنشاء SessionLocal فقط (لا إنشاء session هنا)
        SessionLocal = setup_database()
        
        # ✅ FIX: تمرير SessionLocal بدلاً من session
        services['session_factory'] = SessionLocal
        
        # ✅ FIX: إنشاء المستودعات بدون session (سيتم تمريرها لاحقاً)
        services['recommendation_repo'] = RecommendationRepository()
        services['user_repo'] = UserRepository
        services['channel_repo'] = ChannelRepository
        
        # خدمات البنية التحتية
        services['notifier'] = TelegramNotifier()
        
        # خدمات بينانس (اختيارية)
        binance_creds = None
        if os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_API_SECRET"):
            binance_creds = BinanceCreds(
                api_key=os.getenv("BINANCE_API_KEY"),
                api_secret=os.getenv("BINANCE_API_SECRET")
            )
            log.info("✅ Binance credentials loaded")
        else:
            log.warning("⚠️ Binance credentials not found - auto trading disabled")
        
        services['exec_spot'] = BinanceExec(binance_creds, futures=False) if binance_creds else None
        services['exec_futu'] = BinanceExec(binance_creds, futures=True) if binance_creds else None
        
        # خدمات البيانات
        services['price_service'] = PriceService()
        services['market_data_service'] = MarketDataService()
        
        # خدمات التطبيق
        services['risk_service'] = RiskService(
            exec_spot=services['exec_spot'],
            exec_futu=services['exec_futu']
        )
        
        services['autotrade_service'] = AutoTradeService(
            repo=services['recommendation_repo'],
            notifier=services['notifier'],
            risk=services['risk_service'],
            exec_spot=services['exec_spot'],
            exec_futu=services['exec_futu']
        )
        
        services['report_service'] = ReportService(repo=services['recommendation_repo'])
        services['audit_service'] = AuditService(
            rec_repo=services['recommendation_repo'],
            user_repo_class=services['user_repo']
        )
        
        # خدمات البث والمراقبة
        price_queue = ThreadSafeQueue()
        services['price_streamer'] = PriceStreamer(
            queue=price_queue,
            repo=services['recommendation_repo']
        )
        
        # ✅ FIX: إنشاء AlertService مع trade_service=None مؤقتاً
        services['alert_service'] = AlertService(
            trade_service=None,  # سيتم تعيينه لاحقاً
            price_service=services['price_service'],
            repo=services['recommendation_repo'],
            streamer=services['price_streamer'],
            debounce_seconds=1.0
        )
        
        # الخدمات الرئيسية
        services['analytics_service'] = AnalyticsService(repo=services['recommendation_repo'])
        services['image_parsing_service'] = ImageParsingService()
        
        # ✅ FIX: إنشاء TradeService أخيراً
        services['trade_service'] = TradeService(
            repo=services['recommendation_repo'],
            notifier=services['notifier'],
            market_data_service=services['market_data_service'],
            price_service=services['price_service'],
            alert_service=services['alert_service']
        )
        
        # ✅ FIX: حل التبعية الدائرية
        services['alert_service'].trade_service = services['trade_service']
        
        log.info("✅ All services built successfully")
        
    except Exception as e:
        log.critical(f"❌ Service building failed: {e}", exc_info=True)
        raise
        
    return services

def bootstrap_app() -> Optional[Application]:
    """تهيئة تطبيق Telegram للإنتاج"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not bot_token:
        log.critical("❌ TELEGRAM_BOT_TOKEN is required but not provided")
        return None
    
    log.info(f"✅ Bot token found: {bot_token[:10]}...")
    
    try:
        services = build_services()
        
        # إنشاء تطبيق Telegram
        application = Application.builder().token(bot_token).build()
        
        # ✅ FIX: تسجيل جميع handlers
        from capitalguard.interfaces.telegram.handlers import register_all_handlers
        register_all_handlers(application)
        
        # تخزين الخدمات في bot_data
        application.bot_data.update(services)
        application.bot_data['services'] = services
        
        # حقن تطبيق PTB في الإشعارات
        services['notifier'].set_ptb_app(application)
        
        log.info("✅ Telegram application bootstrapped successfully")
        log.info("✅ All handlers registered successfully")
        
        return application
        
    except Exception as e:
        log.critical(f"❌ Application bootstrap failed: {e}", exc_info=True)
        return None

def get_service_from_context(context, service_name: str, service_type: type) -> Any:
    """الحصول على الخدمات من context"""
    service = context.bot_data.get('services', {}).get(service_name)
    if not service or not isinstance(service, service_type):
        log.error(f"Service {service_name} not found or wrong type")
        raise RuntimeError(f"Service {service_name} unavailable")
    return service

__all__ = ['bootstrap_app', 'build_services', 'get_service_from_context']