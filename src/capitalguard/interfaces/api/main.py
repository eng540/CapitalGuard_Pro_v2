# src/capitalguard/interfaces/api/main.py (Fixed - Version 12.1.2)
"""
إصلاح KeyError: 'services' ومعالجة أخطاء التهيئة
"""

import logging
import asyncio
import html
import json
import traceback
from typing import List

from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from capitalguard.config import settings
from capitalguard.boot import bootstrap_app, build_services, initialize_services
from capitalguard.interfaces.api.deps import get_trade_service, get_analytics_service, require_api_key
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.infrastructure.db.base import get_session

log = logging.getLogger(__name__)

# --- Application Setup ---

app = FastAPI(title="CapitalGuard Pro API", version="12.1.2-fixed")
app.state.ptb_app = None
app.state.services = None

# --- Global Telegram Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج الأخطاء العالمي"""
    log.error("💥 Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    detailed_message = (
        f"🚨 Exception in update handling\n\n"
        f"<b>Update:</b>\n<pre>{html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))[:3500]}</pre>\n\n"
        f"<b>Error:</b>\n<pre>{html.escape(tb_string)}</pre>"
    )

    if settings.TELEGRAM_ADMIN_CHAT_ID and app.state.ptb_app:
        try:
            await app.state.ptb_app.bot.send_message(
                chat_id=settings.TELEGRAM_ADMIN_CHAT_ID, 
                text=detailed_message, 
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.error(f"❌ Failed to send error report to admin: {e}")

    if update and getattr(update, "effective_user", None):
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="⚠️ عذراً، حدث خطأ داخلي. تم إبلاغ الفريق التقني.",
            )
        except Exception as e:
            log.error(f"❌ Failed to notify user {update.effective_user.id}: {e}")


# --- Startup / Shutdown Events ---
@app.on_event("startup")
async def on_startup():
    """بدء التطبيق مع إصلاح KeyError"""
    log.info("🚀 Starting CapitalGuard Pro API...")
    
    ptb_app = bootstrap_app()

    if not ptb_app:
        log.error("❌ Telegram Bot initialization failed. Bot features will be disabled.")
        app.state.ptb_app = None
        app.state.services = build_services()
        # لا توقف التطبيق إذا فشل البوت
        log.info("ℹ️ API will continue without Telegram Bot features")
        return

    app.state.ptb_app = ptb_app
    
    # ✅ الإصلاح الحرج: التحقق من وجود المفتاح "services" قبل الوصول إليه
    try:
        if "services" not in ptb_app.bot_data:
            log.error("❌ Key 'services' not found in bot_data. Using fallback.")
            app.state.services = build_services()
        else:
            app.state.services = ptb_app.bot_data["services"]
            log.info("✅ Services loaded from bot_data")
    except Exception as e:
        log.error(f"❌ Error accessing bot_data: {e}")
        app.state.services = build_services()

    # ✅ التحقق من أن ptb_app صالح قبل إضافة معالج الأخطاء
    if ptb_app:
        try:
            ptb_app.add_error_handler(error_handler)
            log.info("✅ Error handler added to Telegram Bot")
        except Exception as e:
            log.error(f"❌ Failed to add error handler: {e}")

    # تهيئة الخدمات بشكل غير متزامن
    try:
        await initialize_services(ptb_app)
        log.info("✅ Services initialized successfully")
    except Exception as e:
        log.error(f"❌ Service initialization failed: {e}")

    # تهيئة Telegram Bot إذا كان متاحاً
    if ptb_app:
        try:
            await ptb_app.initialize()
            log.info("✅ Telegram Bot initialized")

            private_commands = [
                BotCommand("newrec", "📊 New Recommendation (Menu)"),
                BotCommand("new", "💬 Interactive Builder"),
                BotCommand("rec", "⚡️ Quick Command Mode"),
                BotCommand("editor", "📋 Text Editor Mode"),
                BotCommand("open", "📂 View Open Trades"),
                BotCommand("stats", "📈 View Performance"),
                BotCommand("channels", "📡 Manage Channels"),
                BotCommand("link_channel", "🔗 Link New Channel"),
                BotCommand("cancel", "❌ Cancel Current Operation"),
                BotCommand("help", "ℹ️ Show Help"),
            ]

            if ptb_app.bot and ptb_app.bot.username:
                log.info(f"🤖 Bot username: @{ptb_app.bot.username}")

            await ptb_app.bot.set_my_commands(private_commands)
            log.info("✅ Bot commands configured")

            await ptb_app.start()
            log.info("✅ Telegram Bot started")

            if settings.TELEGRAM_WEBHOOK_URL:
                await ptb_app.bot.set_webhook(
                    url=settings.TELEGRAM_WEBHOOK_URL,
                    allowed_updates=Update.ALL_TYPES
                )
                log.info(f"✅ Webhook set to {settings.TELEGRAM_WEBHOOK_URL}")
                
        except Exception as e:
            log.error(f"❌ Telegram Bot initialization failed: {e}")
            app.state.ptb_app = None  # عطل البوت ولكن استمر في تشغيل API

    log.info("🎉 CapitalGuard Pro API started successfully")


@app.on_event("shutdown")
async def on_shutdown():
    """إيقاف التطبيق بشكل أنيق"""
    log.info("🛑 Shutting down CapitalGuard Pro API...")
    
    try:
        # إيقاف AlertService إذا كان متاحاً
        alert_service = app.state.services.get("alert_service") if app.state.services else None
        if alert_service:
            try:
                alert_service.stop()
                log.info("✅ AlertService stopped")
            except Exception as e:
                log.error(f"❌ Error stopping AlertService: {e}")

        # إيقاف Telegram Bot إذا كان متاحاً
        if app.state.ptb_app:
            try:
                await app.state.ptb_app.stop()
                await app.state.ptb_app.shutdown()
                log.info("✅ Telegram Bot stopped")
            except Exception as e:
                log.error(f"❌ Error stopping Telegram Bot: {e}")
                
    except Exception as e:
        log.error(f"❌ Error during shutdown: {e}")
    finally:
        log.info("✅ Shutdown completed")


# --- باقي الملف يبقى كما هو ---
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    ptb_app = app.state.ptb_app
    if ptb_app:
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
            return {"status": "ok"}
        except Exception as e:
            log.error(f"❌ Error processing Telegram update: {e}")
            return {"status": "error", "message": str(e)}
    else:
        log.warning("⚠️ Telegram Bot not available")
        return {"status": "error", "message": "Telegram Bot not initialized"}

@app.get("/")
def root():
    return {"message": f"🚀 CapitalGuard API v{app.version} is running"}

@app.get("/health", status_code=200, tags=["System"])
def health_check():
    return {"status": "ok"}

# ... باقي النقاط (list_recommendations, close_recommendation, dashboard) تبقى كما هي

app.include_router(auth_router.router)
app.include_router(metrics_router)

log.info("✅ CapitalGuard Pro API module loaded")