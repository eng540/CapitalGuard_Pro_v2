# --- START OF FINAL, RE-ARCHITECTED AND PRODUCTION-READY FILE (Version 8.0.3) ---
# src/capitalguard/interfaces/api/main.py

import logging
import asyncio
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from telegram import Update, BotCommand
from telegram.ext import Application

from capitalguard.config import settings
from capitalguard.boot import bootstrap_app, build_services
from capitalguard.interfaces.api.deps import (
    get_trade_service,
    get_analytics_service,
    get_current_user,
    require_roles
)
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService

# --- Application Setup & Composition Root ---

# The entire setup process is now handled by the central bootstrap function.
ptb_app = bootstrap_app()

# If the bot is disabled, build services without the bot context for the API.
services = ptb_app.bot_data["services"] if ptb_app else build_services()

app = FastAPI(title="CapitalGuard Pro API", version="8.0.3-stable")
app.state.services = services


@app.on_event("startup")
async def on_startup():
    """Handles application startup logic for both FastAPI and the Telegram Bot."""
    market_data_service = app.state.services.get("market_data_service")
    if market_data_service:
        asyncio.create_task(market_data_service.refresh_symbols_cache())
        logging.info("Market data cache refresh task has been scheduled on startup.")
    
    if not ptb_app:
        logging.warning("Telegram Bot Token not provided. Bot features will be disabled.")
        return
      
    await ptb_app.initialize()

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
    
    await ptb_app.start()
      
    if settings.TELEGRAM_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(
            url=settings.TELEGRAM_WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES
        )
        logging.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")

@app.on_event("shutdown")
async def on_shutdown():
    """Handles graceful shutdown."""
    if ptb_app:
        await ptb_app.stop()
        await ptb_app.shutdown()

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """The single endpoint for receiving all updates from the Telegram webhook."""
    if ptb_app:
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception as e:
            logging.exception("Error processing Telegram update: %s", e)
    return {"status": "ok"}

# --- API Endpoints ---
@app.get("/")
def root():
    return {"message": f"🚀 CapitalGuard API v{app.version} is running"}

@app.get("/health", status_code=200, tags=["System"])
def health_check():
    """Simple health check endpoint for container orchestration."""
    return {"status": "ok"}

@app.get("/recommendations", response_model=list[RecommendationOut], dependencies=[Depends(require_roles({"ANALYST", "ADMIN"}))])
def list_recommendations(
    trade_service: TradeService = Depends(get_trade_service),
    symbol: str = Query(None),
    status: str = Query(None)
):
    items = trade_service.repo.list_all(symbol=symbol, status=status)
    return [RecommendationOut.from_orm(item) for item in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_roles({"ANALYST", "ADMIN"}))])
def close_recommendation(
    rec_id: int,
    payload: CloseIn,
    trade_service: TradeService = Depends(get_trade_service),
    current_user: dict = Depends(get_current_user)
):
    try:
        # Note: This is still not multi-tenant safe from the API and needs a user mapping layer.
        rec = trade_service.repo.get(rec_id) 
        if not rec:
            raise ValueError("Recommendation not found")
        closed_rec = trade_service.close_recommendation_for_user(rec.id, rec.user_id, payload.exit_price)
        return RecommendationOut.from_orm(closed_rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_roles({"ANALYST", "ADMIN"}))])
def dashboard(
    analytics_service: AnalyticsService = Depends(get_analytics_service),
    user_id: str = "default_user",
    symbol: str = Query(None),
    status: str = Query(None)
):
    items = analytics_service.repo.list_all_for_user(user_telegram_id=user_id, symbol=symbol, status=status)
    rows = "".join(f"<tr><td>{r.id}</td><td>{r.asset.value}</td><td>{r.side.value}</td><td>{r.status.value}</td></tr>" for r in items)
    html = f"<html><body><h1>Dashboard</h1><table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    return HTMLResponse(content=html)

# --- Include Routers ---
app.include_router(auth_router.router)
app.include_router(metrics_router)
# --- END OF FINAL, RE-ARCHITECTED AND PRODUCTION-READY FILE (Version 8.0.3) ---