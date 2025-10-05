# src/capitalguard/boot.py (v3.2 - Final, DEBUG removed)
"""
Bootstrap and dependency injection setup for CapitalGuard Pro.
This is the single source of truth for service initialization.
"""

import logging
from typing import Dict, Any, Optional

from telegram.ext import Application
from sqlalchemy import create_engine
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
from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.market.ws_client import BinanceWS
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
from capitalguard.application.services.image_parsing_service import ImageParsingService  # ✅ NEW

log = logging.getLogger(__name__)

def setup_database():
    """إعداد وتهيئة قاعدة البيانات"""
    try:
        engine = create_engine(
            settings.DATABASE_URL,
            connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
        )
        
        # إنشاء الجداول إذا لم تكن موجودة
        Base.metadata.create_all(bind=engine)
        
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        log.info("✅ Database setup completed successfully")
        return SessionLocal
        
    except Exception as e:
        log.critical(f"❌ Database setup failed: {e}")
        raise

def build_services() -> Dict[str, Any]:
    """
    بناء وتسجيل جميع خدمات التطبيق.
    هذا هو المصدر الوحيد للحقيقة لتهيئة الخدمات.
    """
    services = {}
    
    try:
        # === 1. إعداد قاعدة البيانات والمستودعات ===
        SessionLocal = setup_database()
        
        # === 2. تهيئة المستودعات ===
        services['recommendation_repo'] = RecommendationRepository()
        services['user_repo'] = UserRepository()
        services['channel_repo'] = ChannelRepository()
        
        # === 3. خدمات البنية التحتية ===
        
        # خدمة الإشعارات
        services['notifier'] = TelegramNotifier()
        
        # خدمات بينانس (إذا كانت بيانات الاعتماد متوفرة)
        binance_creds = None
        if settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
            binance_creds = BinanceCreds(
                api_key=settings.BINANCE_API_KEY,
                api_secret=settings.BINANCE_API_SECRET
            )
        
        services['exec_spot'] = BinanceExec(binance_creds, futures=False) if binance_creds else None
        services['exec_futu'] = BinanceExec(binance_creds, futures=True) if binance_creds else None
        
        # خدمات التسعير والبيانات السوقية
        services['price_service'] = PriceService()
        services['market_data_service'] = MarketDataService()
        
        # === 4. خدمات التطبيق الأساسية ===
        
        # خدمة إدارة المخاطر
        services['risk_service'] = RiskService(
            exec_spot=services['exec_spot'],
            exec_futu=services['exec_futu']
        )
        
        # خدمة التداول الآلي
        services['autotrade_service'] = AutoTradeService(
            repo=services['recommendation_repo'],
            notifier=services['notifier'],
            risk=services['risk_service'],
            exec_spot=services['exec_spot'],
            exec_futu=services['exec_futu']
        )
        
        # خدمة التقارير
        services['report_service'] = ReportService(
            repo=services['recommendation_repo']
        )
        
        # خدمة التدقيق
        services['audit_service'] = AuditService(
            rec_repo=services['recommendation_repo'],
            user_repo_class=services['user_repo']
        )
        
        # === 5. خدمات البث والمراقبة ===
        
        # قائمة الانتظار المشتركة
        price_queue = ThreadSafeQueue()
        
        # بث الأسعار
        services['price_streamer'] = PriceStreamer(
            queue=price_queue,
            repo=services['recommendation_repo']
        )
        
        # خدمة التنبيهات
        services['alert_service'] = AlertService(
            trade_service=None,  # سيتم تعيينها لاحقاً
            price_service=services['price_service'],
            repo=services['recommendation_repo'],
            streamer=services['price_streamer'],
            debounce_seconds=1.0
        )
        
        # === 6. الخدمات الرئيسية ===
        
        # خدمة التحليل
        services['analytics_service'] = AnalyticsService(
            repo=services['recommendation_repo']
        )
        
        # ✅ NEW: خدمة تحليل الصور والنص
        services['image_parsing_service'] = ImageParsingService()
        
        # خدمة التداول (يجب أن تكون الأخيرة بسبب التبعيات الدائرية)
        services['trade_service'] = TradeService(
            repo=services['recommendation_repo'],
            notifier=services['notifier'],
            market_data_service=services['market_data_service'],
            price_service=services['price_service'],
            alert_service=services['alert_service']
        )
        
        # حل التبعية الدائرية في AlertService
        services['alert_service'].trade_service = services['trade_service']
        
        log.info("✅ All services built successfully")
        
    except Exception as e:
        log.critical(f"❌ Service building failed: {e}")
        raise
        
    return services

def bootstrap_app() -> Optional[Application]:
    """
    تهيئة تطبيق التيليجرام وإعداد جميع الخدمات.
    هذه هي نقطة الدخول الرئيسية للتطبيق.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        log.critical("❌ TELEGRAM_BOT_TOKEN is required but not provided")
        return None
        
    try:
        # بناء جميع الخدمات
        services = build_services()
        
        # إنشاء تطبيق التيليجرام
        application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
        
        # تخزين الخدمات في bot_data للوصول العالمي
        application.bot_data.update(services)
        application.bot_data['db_session'] = setup_database
        application.bot_data['services'] = services
        
        # حقن تطبيق PTB في الإشعارات
        services['notifier'].set_ptb_app(application)
        
        log.info("✅ Telegram application bootstrapped successfully")
        return application
        
    except Exception as e:
        log.critical(f"❌ Application bootstrap failed: {e}")
        return None

def get_service_from_context(context, service_name: str, service_type: type) -> Any:
    """
    أداة مساعدة للحصول على الخدمات من context.
    """
    service = context.bot_data.get('services', {}).get(service_name)
    if not service or not isinstance(service, service_type):
        log.error(f"Service {service_name} not found or wrong type")
        raise RuntimeError(f"Service {service_name} unavailable")
    return service

# تصدير للاستخدام في أماكن أخرى
__all__ = ['bootstrap_app', 'build_services', 'get_service_from_context']