#--- START OF FILE: src/capitalguard/interfaces/api/main.py ---
import logging
import json
import io
import csv
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from telegram import Update
from telegram.ext import Application, PicklePersistence

from capitalguard.config import settings
from capitalguard.boot import build_services
from capitalguard.interfaces.api.security.deps import get_current_user, require_roles, is_admin
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.telegram.handlers import register_all_handlers
from capitalguard.interfaces.api.routers import auth as auth_router
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService

# --- Application Setup & Composition Root ---
app = FastAPI(title="CapitalGuard Pro API", version="6.0.0 Final")
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
        await ptb_app.initialize()
        ptb_app.bot_data["services"] = services # Re-inject after persistence might overwrite
        
        alert_service = services["alert_service"]
        alert_service.schedule_job(ptb_app, interval_sec=60)
        
        if settings.TELEGRAM_WEBHOOK_URL:
            await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
            logging.info(f"Telegram webhook set to {settings.TELEGRAM_WEBHOOK_URL}")

    @app.on_event("shutdown")
    async def on_shutdown():
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

@app.get("/recommendations", response_model=list[RecommendationOut])
def list_recommendations(request: Request, user: dict = Depends(get_current_user), symbol: str = Query(None), status: str = Query(None)):
    trade_service: TradeService = request.app.state.services["trade_service"]
    items = trade_service.list_all(symbol=symbol, status=status)
    return [RecommendationOut.from_orm(item) for item in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_roles({"analyst"}))])
def close_recommendation(request: Request, rec_id: int, payload: CloseIn):
    trade_service: TradeService = request.app.state.services["trade_service"]
    try:
        rec = trade_service.close(rec_id, payload.exit_price)
        return RecommendationOut.from_orm(rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(get_current_user)])
def dashboard(request: Request, user: dict = Depends(get_current_user), symbol: str = Query(None), status: str = Query(None)):
    analytics_service: AnalyticsService = request.app.state.services["analytics_service"]
    items = analytics_service.list_filtered(symbol=symbol, status=status)
    rows = "".join(f"<tr><td>{r.id}</td><td>{r.asset.value}</td><td>{r.side.value}</td><td>{r.status}</td></tr>" for r in items)
    html = f"<html><body><h1>Dashboard</h1><table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    return HTMLResponse(content=html)

# Include Authentication Router
app.include_router(auth_router.router)
--- END OF FILE ---```

#### **2. `src/capitalguard/interfaces/telegram/helpers.py` (ملف جديد وأساسي)**
*   **الوصف:** يوفر "نقطة وصول آمنة" وموحدة لجميع المعالجات للوصول إلى الخدمات من `bot_data`.

```python
--- START OF FILE: src/capitalguard/interfaces/telegram/helpers.py ---
from telegram.ext import ContextTypes

def get_service(context: ContextTypes.DEFAULT_TYPE, service_name: str):
    """
    الوصول الآمن والموثوق إلى الخدمات المحقونة في main.py.
    """
    services = context.application.bot_data.get("services")
    if not isinstance(services, dict) or service_name not in services:
        raise RuntimeError(f"Service '{service_name}' not configured. Check main.py boot process.")
    return services[service_name]
#--- END OF FILE ---