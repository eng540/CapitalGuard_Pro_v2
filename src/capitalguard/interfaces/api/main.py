# src/capitalguard/interfaces/api/main.py (Fixed - Back to Original with Minimal Changes)
"""
الإصدار الأصلي مع إصلاح مشكلة الاستيراد فقط
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
from capitalguard.boot import bootstrap_app, build_services, initialize_services  # ✅ تم التصحيح
from capitalguard.interfaces.api.deps import get_trade_service, get_analytics_service, require_api_key
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.trade_service import TradeService  # ✅ استيراد مباشر
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.alert_service import AlertService
from capitalguard.infrastructure.db.base import get_session

log = logging.getLogger(__name__)

# --- Application Setup ---

app = FastAPI(title="CapitalGuard Pro API", version="12.1.1-fixed")
app.state.ptb_app = None
app.state.services = None

# --- Global Telegram Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs all uncaught exceptions from handlers and notifies the admin."""
    log.error("Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    detailed_message = (
        f"An exception was raised while handling an update\n\n"
        f"<b>Update:</b>\n<pre>{html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))[:3500]}</pre>\n\n"
        f"<b>Error:</b>\n<pre>{html.escape(tb_string)}</pre>"
    )

    if settings.TELEGRAM_ADMIN_CHAT_ID and app.state.ptb_app:
        try:
            await app.state.ptb_app.bot.send_message(
                chat_id=settings.TELEGRAM_ADMIN_CHAT_ID, text=detailed_message, parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.error(f"Failed to send detailed error report to admin: {e}")

    if update and getattr(update, "effective_user", None):
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="⚠️ Sorry, an internal error occurred. The development team has been notified.",
            )
        except Exception as e:
            log.error(f"Failed to send error notification to user {update.effective_user.id}: {e}")


# --- Startup / Shutdown Events ---
@app.on_event("startup")
async def on_startup():
    """بدء التطبيق مع إصلاحات بسيطة"""
    log.info("🚀 Starting CapitalGuard Pro API...")
    
    ptb_app = bootstrap_app()

    if not ptb_app:
        log.error("Telegram Bot Token not provided. Bot features will be disabled.")
        app.state.ptb_app = None
        app.state.services = build_services()
        return

    app.state.ptb_app = ptb_app
    app.state.services = ptb_app.bot_data["services"]

    ptb_app.add_error_handler(error_handler)

    # تهيئة الخدمات بشكل غير متزامن
    await initialize_services(ptb_app)

    await ptb_app.initialize()

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
        log.info(f"Bot started with username: @{ptb_app.bot.username}")

    await ptb_app.bot.set_my_commands(private_commands)
    log.info("Custom bot commands have been set.")

    await ptb_app.start()

    if settings.TELEGRAM_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(
            url=settings.TELEGRAM_WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES
        )
        log.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")

    log.info("✅ CapitalGuard Pro API started successfully")


@app.on_event("shutdown")
async def on_shutdown():
    """إيقاف التطبيق"""
    log.info("🛑 Shutting down CapitalGuard Pro API...")
    
    alert_service: AlertService = app.state.services.get("alert_service") if app.state.services else None
    if alert_service:
        try:
            alert_service.stop()
            log.info("✅ AlertService stopped")
        except Exception as e:
            log.error(f"Error stopping AlertService: {e}")

    if app.state.ptb_app:
        try:
            await app.state.ptb_app.stop()
            await app.state.ptb_app.shutdown()
            log.info("✅ Telegram Bot stopped")
        except Exception as e:
            log.error(f"Error stopping Telegram Bot: {e}")

    log.info("✅ Shutdown completed")


# --- باقي الملف يبقى كما هو بدون تغيير ---
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    ptb_app = request.app.state.ptb_app
    if ptb_app:
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception:
            log.exception("Error processing Telegram update in webhook.")
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": f"🚀 CapitalGuard API v{app.version} is running"}

@app.get("/health", status_code=200, tags=["System"])
def health_check():
    return {"status": "ok"}

@app.get("/recommendations", response_model=List[RecommendationOut], dependencies=[Depends(require_api_key)])
def list_recommendations(
    db: Session = Depends(get_session),
    trade_service: TradeService = Depends(get_trade_service),
    symbol: str = Query(None),
    status: str = Query(None)
):
    items = trade_service.repo.list_all(db, symbol=symbol, status=status)
    return [RecommendationOut.from_orm(item) for item in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_api_key)])
async def close_recommendation(
    rec_id: int,
    payload: CloseIn,
    db: Session = Depends(get_session),
    trade_service: TradeService = Depends(get_trade_service)
):
    try:
        rec = trade_service.repo.get(db, rec_id)
        if not rec or not rec.user_id:
            raise ValueError("Recommendation not found or has no associated user.")
        
        closed_rec = await trade_service.close_recommendation_for_user_async(rec_id, rec.user_id, payload.exit_price, db_session=db)
        return RecommendationOut.from_orm(closed_rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error(f"Failed to close recommendation via API: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_api_key)])
def dashboard(
    db: Session = Depends(get_session),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
    user_id: str = "default_user"
):
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

# --- Include Routers ---
app.include_router(auth_router.router)
app.include_router(metrics_router)

log.info("✅ CapitalGuard Pro API module loaded")