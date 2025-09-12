# --- START OF FULL, FINAL, AND READY-TO-USE FILE ---
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

# --- Application Setup & Composition Root ---
app = FastAPI(title="CapitalGuard Pro API", version="8.0.0-stable")
services = build_services()
app.state.services = services

# --- Telegram Bot Setup ---
def create_ptb_app() -> Application:
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    application.bot_data["services"] = services
    register_all_handlers(application)
    return application

ptb_app: Application | None = None
if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = create_ptb_app()

@app.on_event("startup")  
async def on_startup():
    market_data_service = app.state.services.get("market_data_service")
    if market_data_service:
        asyncio.create_task(market_data_service.refresh_symbols_cache())
        logging.info("Market data cache refresh task has been scheduled on startup.")
    else:
        logging.error("MarketDataService not found in app state. Symbol validation will be unreliable.")

    if not ptb_app: return  
      
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
    
    await ptb_app.bot.set_my_commands(private_commands)
    logging.info("Custom bot commands have been set for private chats.")
    
    await ptb_app.start()  
    ptb_app.bot_data["services"] = services  
      
    alert_service = services["alert_service"]  
    alert_service.schedule_job(ptb_app, interval_sec=5)  
      
    if settings.TELEGRAM_WEBHOOK_URL:  
        await ptb_app.bot.set_webhook(  
            url=settings.TELEGRAM_WEBHOOK_URL,   
            allowed_updates=Update.ALL_TYPES  
        )  
        logging.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")  

@app.on_event("shutdown")  
async def on_shutdown():  
    if not ptb_app: return  
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
    items = trade_service.repo.list_all(symbol=symbol, status=status)
    return [RecommendationOut.from_orm(item) for item in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_roles({"ANALYST", "ADMIN"}))])
def close_recommendation(
    rec_id: int,
    payload: CloseIn,
    trade_service: TradeService = Depends(get_trade_service)
):
    try:
        rec = trade_service.close(rec_id, payload.exit_price)
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
    items = analytics_service.list_filtered_for_user(user_id=user_id, symbol=symbol, status=status)
    rows = "".join(f"<tr><td>{r.id}</td><td>{r.asset.value}</td><td>{r.side.value}</td><td>{r.status.value}</td></tr>" for r in items)
    html = f"<html><body><h1>Dashboard</h1><table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    return HTMLResponse(content=html)

# --- Include Routers ---
app.include_router(auth_router.router)
app.include_router(metrics_router)
# --- END OF FULL, FINAL, AND READY-TO-USE FILE ---