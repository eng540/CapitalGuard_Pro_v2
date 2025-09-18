# --- START OF UPDATED FILE WITH GLOBAL ERROR HANDLER (Version 8.2.0) ---
# src/capitalguard/interfaces/api/main.py

import logging
import asyncio
import html
import json
import traceback
from typing import List

from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from telegram import Update, BotCommand, ParseMode
from telegram.ext import Application, ContextTypes

from capitalguard.config import settings
from capitalguard.boot import bootstrap_app, build_services
from capitalguard.interfaces.api.deps import get_trade_service, get_analytics_service, require_api_key
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.infrastructure.db.base import SessionLocal

log = logging.getLogger(__name__)

# --- Application Setup ---

app = FastAPI(title="CapitalGuard Pro API", version="8.2.0-stable")
app.state.ptb_app = None  # Will hold the Telegram bot application
app.state.services = None  # Will hold built services

# --- Global Telegram Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Logs errors and notifies the user/admin via Telegram when exceptions occur in handlers.
    """
    log.error("Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"An exception was raised while handling an update\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}</pre>\n\n"
        f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
        f"<pre>{html.escape(tb_string)}</pre>"
    )

    # Send a friendly message to the user if possible
    if update and getattr(update, "effective_user", None):
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="⚠️ Sorry, an internal error occurred. The admin has been notified.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.error(f"Failed to send error notification to user: {e}")


# --- Startup / Shutdown Events ---
@app.on_event("startup")
async def on_startup():
    """
    Handles application startup logic for FastAPI and Telegram Bot.
    Initializes services, schedules tasks, sets bot commands, and registers the error handler.
    """
    ptb_app = bootstrap_app()

    if not ptb_app:
        logging.warning("Telegram Bot Token not provided. Bot features will be disabled.")
        app.state.ptb_app = None
        app.state.services = build_services()  # Build services without bot context
        return

    # Save bot instance and services
    app.state.ptb_app = ptb_app
    app.state.services = ptb_app.bot_data["services"]

    # Register global error handler
    ptb_app.add_error_handler(error_handler)

    # Schedule market data cache refresh if service exists
    market_data_service = app.state.services.get("market_data_service")
    if market_data_service:
        asyncio.create_task(market_data_service.refresh_symbols_cache())
        logging.info("Market data cache refresh task has been scheduled on startup.")

    await ptb_app.initialize()

    # Set custom bot commands
    private_commands = [
        BotCommand("newrec", "📊 بدء إنشاء توصية جديدة (القائمة)"),
        BotCommand("new", "💬 بدء المنشئ التفاعلي مباشرة"),
        BotCommand("rec", "⚡️ استخدام وضع الأمر السريع"),
        BotCommand("editor", "📋 استخدام المحرر النصي"),
        BotCommand("open", "📂 عرض التوصيات المفتوحة"),
        BotCommand("stats", "📈 عرض ملخص الأداء"),
        BotCommand("channels", "📡 إدارة قنوات النشر"),
        BotCommand("link_channel", "🔗 ربط قناة جديدة"),
        BotCommand("cancel", "❌ إلغاء العملية الحالية"),
        BotCommand("help", "ℹ️ عرض المساعدة"),
    ]

    if ptb_app.bot and ptb_app.bot.username:
        logging.info(f"Bot started with username: @{ptb_app.bot.username}")

    await ptb_app.bot.set_my_commands(private_commands)
    logging.info("Custom bot commands have been set for private chats.")

    # Schedule alert jobs
    alert_service = app.state.services.get("alert_service")
    if alert_service:
        alert_service.schedule_job(ptb_app, interval_sec=5)

    await ptb_app.start()

    if settings.TELEGRAM_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(
            url=settings.TELEGRAM_WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES
        )
        logging.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")


@app.on_event("shutdown")
async def on_shutdown():
    """Handles graceful shutdown for the Telegram bot."""
    if app.state.ptb_app:
        await app.state.ptb_app.stop()
        await app.state.ptb_app.shutdown()


# --- Webhook Endpoint ---
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """The single endpoint for receiving all updates from the Telegram webhook."""
    ptb_app = request.app.state.ptb_app
    if ptb_app:
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception as e:
            log.exception("Error processing Telegram update: %s", e)
    return {"status": "ok"}


# --- API Endpoints ---
@app.get("/")
def root():
    return {"message": f"🚀 CapitalGuard API v{app.version} is running"}

@app.get("/health", status_code=200, tags=["System"])
def health_check():
    return {"status": "ok"}

@app.get("/recommendations", response_model=List[RecommendationOut], dependencies=[Depends(require_api_key)])
def list_recommendations(
    trade_service: TradeService = Depends(get_trade_service),
    symbol: str = Query(None),
    status: str = Query(None)
):
    with SessionLocal() as session:
        items = trade_service.repo.list_all(session, symbol=symbol, status=status)
        return [RecommendationOut.from_orm(item) for item in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_api_key)])
async def close_recommendation(
    rec_id: int,
    payload: CloseIn,
    trade_service: TradeService = Depends(get_trade_service)
):
    try:
        with SessionLocal() as session:
            rec = trade_service.repo.get(session, rec_id)
        if not rec or not rec.user_id:
            raise ValueError("Recommendation not found or has no associated user.")
        closed_rec = await trade_service.close_recommendation_for_user_async(rec.id, rec.user_id, payload.exit_price)
        return RecommendationOut.from_orm(closed_rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error(f"Failed to close recommendation via API: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_api_key)])
def dashboard(
    analytics_service: AnalyticsService = Depends(get_analytics_service),
    user_id: str = "default_user",
    symbol: str = Query(None),
    status: str = Query(None)
):
    items = analytics_service.get_all_for_user(user_id, symbol=symbol, status=status)
    rows = "".join(f"<tr><td>{r.id}</td><td>{r.asset.value}</td><td>{r.side.value}</td><td>{r.status.value}</td></tr>" for r in items)
    html_content = f"<html><body><h1>Dashboard</h1><table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    return HTMLResponse(content=html_content)


# --- Include Routers ---
app.include_router(auth_router.router)
app.include_router(metrics_router)

# --- END OF UPDATED FILE WITH GLOBAL ERROR HANDLER (Version 8.2.0) ---