# src/capitalguard/boot.py (v19.0.8 - Fixed & Enhanced)
"""
الإصدار العامل مع إصلاحات طفيفة للفشل الصامت
"""

import os
import logging
import sys
import threading
import asyncio
from typing import Dict, Any, Optional

from telegram.ext import Application, PicklePersistence

from capitalguard.config import settings
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, BinanceCreds
from capitalguard.interfaces.telegram.handlers import register_all_handlers
from capitalguard.service_registry import register_global_services

class TelegramLogHandler(logging.Handler):
    """معالج تسجيل لإرسال الرسائل الحرجة إلى Telegram"""
    def __init__(self, notifier: TelegramNotifier, main_loop: asyncio.AbstractEventLoop, level=logging.ERROR):
        super().__init__(level=level)
        self.notifier = notifier
        self.main_loop = main_loop
        self._local = threading.local()
        self._local.is_handling = False

    def emit(self, record: logging.LogRecord):
        if getattr(self._local, 'is_handling', False):
            return
        
        if not self.notifier or not settings.TELEGRAM_ADMIN_CHAT_ID:
            return
        
        try:
            self._local.is_handling = True
            simple_message = f"⚠️ CRITICAL ERROR: {record.getMessage()}"
            admin_chat_id = int(settings.TELEGRAM_ADMIN_CHAT_ID)
            if hasattr(self.notifier, 'send_private_text'):
                coro = self.notifier.send_private_text(chat_id=admin_chat_id, text=simple_message)
                if asyncio.iscoroutine(coro):
                    asyncio.run_coroutine_threadsafe(coro, self.main_loop)
        except Exception as e:
            root_logger = logging.getLogger()
            root_logger.removeHandler(self)
            root_logger.error(f"CRITICAL: Failed to send log to Telegram: {e}", exc_info=False)
            root_logger.addHandler(self)
        finally:
            self._local.is_handling = False

def setup_logging(notifier: Optional[TelegramNotifier] = None, main_loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """تهيئة التسجيل للتطبيق"""
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    if notifier and settings.TELEGRAM_ADMIN_CHAT_ID and main_loop:
        telegram_handler = TelegramLogHandler(notifier, main_loop)
        telegram_handler.setLevel(logging.ERROR)
        root_logger.addHandler(telegram_handler)
        logging.info("TelegramLogHandler configured for admin notifications.")
    else:
        logging.warning("TelegramLogHandler is not configured (TELEGRAM_ADMIN_CHAT_ID or main_loop not set).")

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.info("Logging configured successfully.")

def build_services(ptb_app: Optional[Application] = None) -> Dict[str, Any]:
    """بناء الخدمات وتهيئة السجل العالمي"""
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    if ptb_app:
        notifier.set_ptb_app(ptb_app)
    
    main_loop = asyncio.get_event_loop()
    setup_logging(notifier, main_loop)

    market_data_service = MarketDataService()
    price_service = PriceService()
    analytics_service = AnalyticsService(repo=repo)
    
    # ✅ إنشاء AlertService مع main_loop صحيح
    alert_service = AlertService(
        trade_service=None,  # سيتم تعيينه لاحقاً
        repo=repo,
        notifier=notifier,
        admin_chat_id=settings.TELEGRAM_ADMIN_CHAT_ID,
        main_loop=main_loop
    )
    
    # ✅ إنشاء TradeService مع ربط AlertService
    trade_service = TradeService(
        repo=repo, 
        notifier=notifier, 
        market_data_service=market_data_service, 
        price_service=price_service,
        alert_service=alert_service  # ✅ تم التصحيح
    )
    
    # ✅ ربط الخدمات ببعضها
    alert_service.trade_service = trade_service
    
    services = {
        "trade_service": trade_service,
        "analytics_service": analytics_service,
        "price_service": price_service,
        "alert_service": alert_service,
        "notifier": notifier,
        "market_data_service": market_data_service,
    }

    register_global_services(services)
    
    logging.info(f"✅ Built {len(services)} services successfully")
    return services

def bootstrap_app() -> Optional[Application]:
    """تهيئة تطبيق Telegram bot"""
    if not settings.TELEGRAM_BOT_TOKEN:
        logging.error("❌ TELEGRAM_BOT_TOKEN not provided")
        return None
        
    try:
        persistence = PicklePersistence(filepath="./telegram_bot_persistence")
        ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
        
        # ✅ بناء الخدمات وإضافتها إلى bot_data
        services = build_services(ptb_app)
        ptb_app.bot_data["services"] = services  # ✅ هذا السطر الحاسم كان مفقوداً
        logging.info("✅ Services added to bot_data")

        register_all_handlers(ptb_app)
        logging.info("✅ Telegram bot bootstrapped successfully.")
        return ptb_app
        
    except Exception as e:
        logging.exception(f"💥 CRITICAL: Failed to bootstrap bot: {e}")
        return None

# ✅ إضافة دالة initialize_services للتوافق مع main.py
async def initialize_services(ptb_app: Application):
    """تهيئة الخدمات بشكل غير متزامن للتوافق مع main.py"""
    if not ptb_app:
        logging.warning("⚠️ Cannot initialize services: ptb_app is None")
        return
        
    try:
        services = ptb_app.bot_data.get("services", {})
        alert_service = services.get("alert_service")
        market_data_service = services.get("market_data_service")
        
        logging.info("🔄 Starting async service initialization...")
        
        # 1. تحديث Market Data Cache
        if market_data_service:
            logging.info("📊 Refreshing market data cache...")
            try:
                await market_data_service.refresh_symbols_cache()
                logging.info("✅ Market data cache refreshed")
            except Exception as e:
                logging.error(f"❌ Market data cache refresh failed: {e}")
        
        # 2. بناء فهرس المحفزات
        if alert_service:
            logging.info("🔍 Building triggers index...")
            try:
                await alert_service.build_triggers_index()
                logging.info("✅ Triggers index built")
            except Exception as e:
                logging.error(f"❌ Triggers index build failed: {e}")
        
        # 3. بدء AlertService
        if alert_service:
            alert_service.start()
            logging.info("✅ AlertService started")
            
        logging.info("🎉 Service initialization completed")
        
    except Exception as e:
        logging.error(f"❌ Service initialization failed: {e}")