# src/capitalguard/interfaces/api/main.py (Version 13.0.0 - Enhanced)
"""
FastAPI Main Entry Point - مع إصلاحات شاملة للفشل الصامت
"""

import logging
import asyncio
import html
import json
import traceback
from typing import List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from capitalguard.config import settings
from capitalguard.boot import bootstrap_app, initialize_services
from capitalguard.interfaces.api.deps import get_trade_service, get_analytics_service, require_api_key
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.infrastructure.db.base import get_session

log = logging.getLogger(__name__)

# Global variables to hold application state
ptb_application = None
application_services = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """إدارة دورة حياة التطبيق مع تحسينات الموثوقية"""
    # Startup
    await startup_event()
    try:
        yield
    finally:
        # Shutdown
        await shutdown_event()

# إنشاء تطبيق FastAPI مع lifespan management
app = FastAPI(
    title="CapitalGuard Pro API", 
    version="13.0.0-enhanced",
    lifespan=lifespan
)

# --- Global Telegram Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج الأخطاء العالمي مع تحسينات"""
    log.error("💥 Exception while handling an update:", exc_info=context.error)

    # إنشاء تقرير خطأ مفصل
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    detailed_message = (
        f"🚨 Exception in update handling\n\n"
        f"<b>Update:</b>\n<pre>{html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))[:3500]}</pre>\n\n"
        f"<b>Error:</b>\n<pre>{html.escape(tb_string)}</pre>"
    )

    # إرسال تنبيه للمسؤول
    if settings.TELEGRAM_ADMIN_CHAT_ID and ptb_application:
        try:
            await ptb_application.bot.send_message(
                chat_id=settings.TELEGRAM_ADMIN_CHAT_ID, 
                text=detailed_message, 
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.error(f"❌ Failed to send error report to admin: {e}")

    # إعلام المستخدم
    if update and getattr(update, "effective_user", None):
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="⚠️ عذراً، حدث خطأ داخلي. تم إبلاغ الفريق التقني.",
            )
        except Exception as e:
            log.error(f"❌ Failed to notify user {update.effective_user.id}: {e}")

# --- Startup / Shutdown Events ---
async def startup_event():
    """بدء التطبيق مع التحسينات"""
    global ptb_application, application_services
    
    log.info("🚀 Starting CapitalGuard Pro API...")
    
    # 1. تهيئة Telegram Bot
    ptb_app = bootstrap_app()
    
    if not ptb_app:
        log.critical("❌ Telegram Bot initialization failed. Bot features disabled.")
        ptb_application = None
        application_services = {}
        return

    ptb_application = ptb_app
    application_services = ptb_app.bot_data

    # 2. إعداد معالج الأخطاء
    ptb_app.add_error_handler(error_handler)

    # 3. تهيئة الخدمات بشكل غير متزامن
    await initialize_services(ptb_app)

    # 4. إعداد أوامر البوت
    await setup_bot_commands(ptb_app)

    # 5. إعداد Webhook إذا كان مفعلاً
    await setup_webhook(ptb_app)

    # 6. بدء البوت
    await ptb_app.initialize()
    await ptb_app.start()
    
    log.info("✅ CapitalGuard Pro API started successfully")

async def shutdown_event():
    """إيقاف التطبيق بشكل أنيق"""
    global ptb_application, application_services
    
    log.info("🛑 Shutting down CapitalGuard Pro API...")
    
    try:
        # 1. إيقاف AlertService
        alert_service = application_services.get("alert_service") if application_services else None
        if alert_service:
            alert_service.stop()
            log.info("✅ AlertService stopped")

        # 2. إيقاف System Monitor
        system_monitor = application_services.get("system_monitor") if application_services else None
        if system_monitor:
            system_monitor.stop()
            log.info("✅ System monitor stopped")

        # 3. إيقاف Telegram Bot
        if ptb_application:
            await ptb_application.stop()
            await ptb_application.shutdown()
            log.info("✅ Telegram Bot stopped")

        log.info("✅ CapitalGuard Pro API shutdown completed")
        
    except Exception as e:
        log.error(f"❌ Error during shutdown: {e}")
    finally:
        ptb_application = None
        application_services = None

async def setup_bot_commands(ptb_app: Application):
    """إعداد أوامر البوت"""
    try:
        private_commands = [
            BotCommand("newrec", "📊 إنشاء توصية جديدة (قائمة)"),
            BotCommand("new", "💬 المنشئ التفاعلي"),
            BotCommand("rec", "⚡️ الوضع السريع"),
            BotCommand("editor", "📋 محرر النصوص"),
            BotCommand("open", "📂 التوصيات المفتوحة"),
            BotCommand("stats", "📈 الإحصائيات"),
            BotCommand("channels", "📡 إدارة القنوات"),
            BotCommand("link_channel", "🔗 ربط قناة جديدة"),
            BotCommand("cancel", "❌ إلغاء العملية"),
            BotCommand("help", "ℹ️ المساعدة"),
        ]

        if ptb_app.bot and ptb_app.bot.username:
            log.info(f"🤖 Bot started with username: @{ptb_app.bot.username}")

        await ptb_app.bot.set_my_commands(private_commands)
        log.info("✅ Bot commands configured")
        
    except Exception as e:
        log.error(f"❌ Failed to setup bot commands: {e}")

async def setup_webhook(ptb_app: Application):
    """إعداد Webhook إذا كان مفعلاً"""
    if settings.TELEGRAM_WEBHOOK_URL:
        try:
            await ptb_app.bot.set_webhook(
                url=settings.TELEGRAM_WEBHOOK_URL,
                allowed_updates=Update.ALL_TYPES
            )
            log.info(f"✅ Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")
        except Exception as e:
            log.error(f"❌ Failed to set webhook: {e}")

# --- Webhook Endpoint ---
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """نقطة الوصول لـ Telegram Webhook"""
    if not ptb_application:
        return {"status": "error", "message": "Telegram Bot not initialized"}
    
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_application.bot)
        await ptb_application.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        log.error(f"❌ Error processing Telegram update: {e}")
        return {"status": "error", "message": str(e)}

# --- API Endpoints ---
@app.get("/")
def root():
    """الصفحة الرئيسية"""
    return {
        "message": f"🚀 CapitalGuard API v{app.version} is running",
        "status": "healthy",
        "services": {
            "telegram_bot": ptb_application is not None,
            "alert_service": application_services.get("alert_service")._is_running if application_services and application_services.get("alert_service") else False,
            "system_monitor": application_services.get("system_monitor")._is_running if application_services and application_services.get("system_monitor") else False,
        } if application_services else {}
    }

@app.get("/health", status_code=200, tags=["System"])
def health_check():
    """فحص صحة النظام"""
    health_status = {
        "status": "healthy",
        "timestamp": asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else None,
        "services": {}
    }
    
    if application_services:
        # فحص AlertService
        alert_service = application_services.get("alert_service")
        if alert_service:
            health_status["services"]["alert_service"] = {
                "running": getattr(alert_service, '_is_running', False),
                "queue_size": alert_service.price_queue.qsize() if hasattr(alert_service, 'price_queue') else 0,
                "total_processed": getattr(alert_service.health_monitor, 'total_processed', 0) if hasattr(alert_service, 'health_monitor') else 0
            }
        
        # فحص System Monitor
        system_monitor = application_services.get("system_monitor")
        if system_monitor:
            health_status["services"]["system_monitor"] = {
                "running": getattr(system_monitor, '_is_running', False)
            }
    
    return health_status

@app.get("/debug/triggers", dependencies=[Depends(require_api_key)])
async def debug_triggers():
    """نقطة تفيدية لفحص المحفزات النشطة"""
    if not application_services:
        raise HTTPException(status_code=503, detail="Services not available")
    
    alert_service = application_services.get("alert_service")
    if not alert_service:
        raise HTTPException(status_code=503, detail="AlertService not available")
    
    try:
        # بناء فهرس جديد للحصول على أحدث البيانات
        await alert_service.build_triggers_index()
        
        triggers_info = {
            "total_symbols": len(alert_service.active_triggers),
            "symbols": list(alert_service.active_triggers.keys()),
            "total_triggers": sum(len(triggers) for triggers in alert_service.active_triggers.values()),
            "queue_size": alert_service.price_queue.qsize(),
            "health": {
                "total_processed": alert_service.health_monitor.total_processed,
                "last_processed": time.time() - alert_service.health_monitor.last_processed_time if hasattr(alert_service.health_monitor, 'last_processed_time') else None
            }
        }
        
        return triggers_info
    except Exception as e:
        log.error(f"❌ Debug triggers error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# النقاط الأخرى تبقى كما هي (list_recommendations, close_recommendation, dashboard)
@app.get("/recommendations", response_model=List[RecommendationOut], dependencies=[Depends(require_api_key)])
def list_recommendations(
    db: Session = Depends(get_session),
    trade_service: TradeService = Depends(get_trade_service),
    symbol: str = Query(None),
    status: str = Query(None)
):
    """قائمة التوصيات"""
    try:
        items = trade_service.repo.list_all(db, symbol=symbol, status=status)
        return [RecommendationOut.from_orm(item) for item in items]
    except Exception as e:
        log.error(f"❌ Error listing recommendations: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_api_key)])
async def close_recommendation(
    rec_id: int,
    payload: CloseIn,
    db: Session = Depends(get_session),
    trade_service: TradeService = Depends(get_trade_service)
):
    """إغلاق توصية"""
    try:
        rec = trade_service.repo.get(db, rec_id)
        if not rec or not rec.user_id:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        
        closed_rec = await trade_service.close_recommendation_for_user_async(rec_id, rec.user_id, payload.exit_price, db_session=db)
        return RecommendationOut.from_orm(closed_rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error(f"❌ Error closing recommendation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_api_key)])
def dashboard(
    db: Session = Depends(get_session),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
    user_id: str = "default_user"
):
    """لوحة التحكم"""
    try:
        summary = analytics_service.performance_summary_for_user(db, user_id)
        
        html_content = f"""
        <html>
            <head><title>Dashboard for User {user_id}</title></head>
            <body>
                <h1>Performance Summary for User: {user_id}</h1>
                <ul>
                    <li>Total Recommendations: {summary.get('total_recommendations', 'N/A')}</li>
                    <li>Open Recommendations: {summary.get('open_recommendations', 'N/A')}</li>
                    <li>Closed Recommendations: {summary.get('closed_recommendations', 'N/A')}</li>
                    <li><b>Overall Win Rate: {summary.get('overall_win_rate', 'N/A')}</b></li>
                    <li><b>Total PnL (Percent): {summary.get('total_pnl_percent', 'N/A')}</b></li>
                </ul>
            </body>
        </html>
        """
        return HTMLResponse(content=html_content)
    except Exception as e:
        log.error(f"❌ Dashboard error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# --- Include Routers ---
app.include_router(auth_router.router)
app.include_router(metrics_router)

# إضافة نقطة للتحقق من حالة Webhook
@app.get("/webhook/info", dependencies=[Depends(require_api_key)])
async def webhook_info():
    """معلومات Webhook"""
    if not ptb_application or not ptb_application.bot:
        return {"status": "bot_not_initialized"}
    
    try:
        webhook_info = await ptb_application.bot.get_webhook_info()
        return {
            "status": "ok",
            "webhook_info": {
                "url": webhook_info.url,
                "has_custom_certificate": webhook_info.has_custom_certificate,
                "pending_update_count": webhook_info.pending_update_count,
                "ip_address": webhook_info.ip_address,
                "last_error_date": webhook_info.last_error_date,
                "last_error_message": webhook_info.last_error_message,
                "max_connections": webhook_info.max_connections,
                "allowed_updates": webhook_info.allowed_updates,
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# تحسينات للأداء
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware لتسجيل الطلبات"""
    start_time = asyncio.get_event_loop().time()
    
    response = await call_next(request)
    
    process_time = asyncio.get_event_loop().time() - start_time
    log.info(f"📨 {request.method} {request.url.path} - Status: {response.status_code} - Time: {process_time:.2f}s")
    
    return response

log.info("✅ CapitalGuard Pro API module loaded successfully")