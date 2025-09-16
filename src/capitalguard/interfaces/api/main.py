# --- START OF FULL, FINAL, AND READY-TO-USE FILE (Version 8.0.1-hotfix) ---
import logging
import asyncio
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from telegram import Update, BotCommand
from telegram.ext import Application, PicklePersistence

from capitalguard.config import settings
from capitalguard.boot import build_services
from capitalguard.interfaces.api.deps import (
    get_trade_service, 
    get_analytics_service, 
    get_current_user,
    require_roles
)
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.telegram.handlers import register_all_handlers
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.interfaces.api.metrics import router as metrics_router
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService

# --- Telegram Bot and Services Setup ---
# ✅ FIX: Create the PTB application instance first. This instance is the source of truth
# for bot-related context that other services might need.
ptb_app: Application | None = None
if settings.TELEGRAM_BOT_TOKEN:
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()

# ✅ FIX: Build services AFTER the ptb_app is created, and pass it in.
# This allows the Composition Root (build_services) to inject the ptb_app
# into any service that needs it, like the TelegramNotifier, solving the dependency problem.
services = build_services(ptb_app)

# ✅ FIX: Now that services (including the configured notifier) are ready,
# inject them into the bot's context data and register handlers.
if ptb_app:
    ptb_app.bot_data["services"] = services
    register_all_handlers(ptb_app)

# --- FastAPI Application Setup ---
app = FastAPI(title="CapitalGuard Pro API", version="8.0.1-hotfix")
app.state.services = services

@app.on_event("startup")  
async def on_startup():
    market_data_service = app.state.services.get("market_data_service")
    if market_data_service:
        asyncio.create_task(market_data_service.refresh_symbols_cache())
        logging.info("Market data cache refresh task has been scheduled on startup.")
    
    if not ptb_app: 
        logging.warning("Telegram Bot Token not provided. Bot features will be disabled.")
        return  
      
    await ptb_app.initialize()  

    # Define and set the bot commands that will appear in the Telegram interface
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
    
    # After initialization, the bot's info (like username) is available.
    if ptb_app.bot.username:
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
    if ptb_app:
        await ptb_app.stop()  
        await ptb_app.shutdown()

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
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
    # This endpoint now relies on the user's JWT for authorization,
    # but the underlying service layer needs the user's Telegram ID for data filtering.
    # A complete solution would map the JWT subject (email) to a user record to get the Telegram ID.
    # For now, this remains a gap to be addressed in the multi-tenancy implementation.
    items = trade_service.repo.list_all(symbol=symbol, status=status) # Note: This is not yet multi-tenant safe.
    return [RecommendationOut.from_orm(item) for item in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_roles({"ANALYST", "ADMIN"}))])
def close_recommendation(
    rec_id: int,
    payload: CloseIn,
    trade_service: TradeService = Depends(get_trade_service),
    current_user: dict = Depends(get_current_user) # Placeholder for getting user context
):
    try:
        # To be fully secure, we'd need to get the user's telegram_id from their email (current_user.sub)
        # and pass it to the service. This is a placeholder for that logic.
        # user_telegram_id = map_email_to_telegram_id(current_user.sub)
        # rec = trade_service.close_recommendation_for_user(rec_id, user_telegram_id, payload.exit_price)
        
        # Current implementation is not multi-tenant safe from the API side:
        rec = trade_service.close(rec_id, payload.exit_price) # This is the old, insecure method call
        return RecommendationOut.from_orm(rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_roles({"ANALYST", "ADMIN"}))])
def dashboard(
    analytics_service: AnalyticsService = Depends(get_analytics_service),
    user_id: str = "default_user",
    symbol: str = Query(None),
    status: str = Query(None)
):
    # This endpoint is for demonstration and not multi-tenant aware.
    items = analytics_service.repo.list_all_for_user(user_telegram_id=user_id, symbol=symbol, status=status)
    rows = "".join(f"<tr><td>{r.id}</td><td>{r.asset.value}</td><td>{r.side.value}</td><td>{r.status.value}</td></tr>" for r in items)
    html = f"<html><body><h1>Dashboard</h1><table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    return HTMLResponse(content=html)

# --- Include Routers ---
app.include_router(auth_router.router)
app.include_router(metrics_router)
# --- END OF FULL, FINAL, AND READY-TO-USE FILE (Version 8.0.1-hotfix) ---