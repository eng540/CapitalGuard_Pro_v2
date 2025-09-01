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

# --- التطبيق وجذر التجميع ---
app = FastAPI(title="CapitalGuard Pro API", version="6.0.0 Final")
services = build_services()
app.state.services = services

# --- إعداد بوت تليجرام ---
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
        # إعادة حقن الخدمات بعد التهيئة (مهم لـ PicklePersistence)
        ptb_app.bot_data["services"] = services
        # تفعيل خدمة التنبيهات
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

# --- نقاط النهاية الأساسية ---
@app.get("/")
def root():
    return {"message": f"🚀 CapitalGuard API v{app.version} is running"}

@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": app.version, "env": settings.ENV}

# --- نقاط النهاية للتوصيات (محمية بـ JWT) ---
@app.get("/recommendations", response_model=list[RecommendationOut])
def list_recommendations(
    request: Request,
    user: dict = Depends(get_current_user),
    symbol: str = Query(None),
    status: str = Query(None)
):
    trade_service = request.app.state.services["trade_service"]
    items = trade_service.list_all(symbol=symbol, status=status)
    return [RecommendationOut.from_orm(item) for item in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_roles({"analyst"}))])
def close_recommendation(request: Request, rec_id: int, payload: CloseIn):
    trade_service = request.app.state.services["trade_service"]
    try:
        rec = trade_service.close(rec_id, payload.exit_price)
        return RecommendationOut.from_orm(rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# --- لوحة التحكم والتقارير (محمية بـ JWT) ---
@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(get_current_user)])
def dashboard(request: Request, symbol: str = Query(None), status: str = Query(None)):
    analytics_service = request.app.state.services["analytics_service"]
    items = analytics_service.list_filtered(symbol=symbol, status=status)
    rows = "".join(
        f"<tr><td>{r.id}</td><td>{r.asset.value}</td><td>{r.side.value}</td><td>{r.status}</td></tr>"
        for r in items
    )
    html = f"<html><body><h1>Dashboard</h1><table><thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    return HTMLResponse(content=html)

@app.get("/report", dependencies=[Depends(get_current_user)])
def get_report(request: Request):
    analytics_service = request.app.state.services["analytics_service"]
    items = analytics_service.list_filtered()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Asset", "Side", "Status", "Entry", "SL", "Exit", "PnL %"])
    for item in items:
        pnl = analytics_service._pnl_percent(item.side.value, item.entry.value, item.exit_price) if item.exit_price else None
        writer.writerow([item.id, item.asset.value, item.side.value, item.status, item.entry.value, item.stop_loss.value, item.exit_price, pnl])
    
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=report.csv"})

# --- نقاط النهاية للتداول الآلي (محمية بـ JWT للأدمن) ---
@app.get("/risk/size", dependencies=[Depends(require_roles({"admin"}))])
def risk_size(request: Request, symbol: str, side: str, market: str, entry: float, sl: float, risk_pct: float = 1.0):
    risk_service = request.app.state.services["risk_service"]
    autotrade_service = request.app.state.services["autotrade_service"]
    ex = autotrade_service.exec_spot if market.lower().startswith("spot") else autotrade_service.exec_futu
    balance = ex.account_balance() or 0.0
    if balance <= 0:
        raise HTTPException(status_code=400, detail="No balance or credentials error")
    result = risk_service.compute_qty(symbol=symbol, side=side, market=market, account_usdt=balance, risk_pct=risk_pct, entry=entry, sl=sl)
    return result.__dict__

@app.post("/autotrade/execute/{rec_id}", dependencies=[Depends(require_roles({"admin"}))])
def autotrade_execute(request: Request, rec_id: int, risk_pct: float = Query(None)):
    autotrade_service = request.app.state.services["autotrade_service"]
    result = autotrade_service.execute_for_rec(rec_id, override_risk_pct=risk_pct)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("msg", "Execution failed"))
    return result

# --- تسجيل الراوترات ---
app.include_router(auth_router.router)
--- END OF FILE ---```

