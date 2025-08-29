# --- START OF FILE: src/capitalguard/interfaces/api/main.py ---
import logging
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse
from telegram import Update
from telegram.ext import Application, PicklePersistence

from capitalguard.config import settings
from capitalguard.boot import build_services
from capitalguard.interfaces.api.deps import require_api_key
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.telegram.handlers import register_all_handlers

app = FastAPI(title="CapitalGuard Pro API", version="4.1.0")

_services_pack: dict = build_services()
app.state.services = _services_pack

ptb_app: Application | None = None

def _build_ptb_app() -> Application:
    persistence = PicklePersistence(filepath="./telegram_bot_persistence")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    application.bot_data.update(_services_pack)
    register_all_handlers(application, _services_pack)
    return application

if settings.TELEGRAM_BOT_TOKEN:
    ptb_app = _build_ptb_app()

    @app.on_event("startup")
    async def on_startup():
        await ptb_app.initialize()
        ptb_app.bot_data.update(_services_pack)
        if settings.TELEGRAM_WEBHOOK_URL:
            await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)

    @app.on_event("shutdown")
    async def on_shutdown():
        try:
            if settings.TELEGRAM_WEBHOOK_URL:
                await ptb_app.bot.delete_webhook()
        except Exception:
            pass
        await ptb_app.shutdown()

    @app.post("/webhook/telegram")
    async def telegram_webhook(request: Request):
        if not ptb_app:
            return JSONResponse({"status": "disabled"}, status_code=503)
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception as e:
            logging.exception("Error processing Telegram update: %s", e)
        return {"status": "ok"}
else:
    logging.warning("TELEGRAM_BOT_TOKEN not set; Telegram features disabled.")

# -------- REST --------
@app.get("/recommendations", response_model=list[RecommendationOut], dependencies=[Depends(require_api_key)])
def list_recs(request: Request,
              channel_id: int | None = None,
              symbol: str | None = Query(None),
              status: str | None = Query(None)):
    trade = request.app.state.services["trade_service"]
    items = trade.list_all(channel_id=channel_id, symbol=symbol, status=status)
    return [RecommendationOut.model_validate(i) for i in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_api_key)])
def close_rec(request: Request, rec_id: int, payload: CloseIn):
    trade = request.app.state.services["trade_service"]
    try:
        rec = trade.close(rec_id, payload.exit_price)
        return RecommendationOut.model_validate(rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, symbol: str | None = None, status: str | None = None):
    trade = request.app.state.services["trade_service"]
    items = trade.list_all(symbol=symbol, status=status)

    rows = []
    for r in items:
        rid   = getattr(r, "id", "")
        asset = getattr(r, "asset", "")
        side  = getattr(r, "side", "")
        mkt   = getattr(r, "market", "") or "-"
        st    = getattr(r, "status", "")
        entry = getattr(r, "entry", "")
        sl    = getattr(r, "stop_loss", "")
        exitp = getattr(r, "exit_price", "") or "-"

        rows.append(
            f"<tr>"
            f"<td>#{rid}</td><td>{asset}</td><td>{mkt}/{side}</td>"
            f"<td class='{st}'>{st}</td>"
            f"<td>{entry}</td><td>{sl}</td><td>{exitp}</td></tr>"
        )

    empty = "<tr><td colspan='7' style='text-align:center;color:#777'>لا توجد بيانات بعد</td></tr>"

    html = f"""
<!doctype html>
<html lang="ar" dir="rtl">
  <head>
    <meta charset="utf-8">
    <title>CapitalGuard — لوحة القراءة</title>
    <style>
      body {{font-family: system-ui, -apple-system, Segoe UI, Roboto; margin:24px; background:#fafafa}}
      h2 {{margin: 0 0 16px}}
      .wrap {{background:#fff; border:1px solid #eee; border-radius:12px; padding:16px}}
      table {{border-collapse: collapse; width: 100%}}
      th, td {{border-bottom: 1px solid #eee; padding:10px; text-align:right; font-size:14px}}
      th {{background:#fcfcfc; font-weight:600}}
      tr:hover {{background:#fcfcff}}
      td.OPEN {{color:#0a7a3d; font-weight:600}}
      td.CLOSED {{color:#b51d1d; font-weight:600}}
      .hint {{color:#888; font-size:13px; margin-top:8px}}
    </style>
  </head>
  <body>
    <div class="wrap">
      <h2>📋 قائمة التوصيات</h2>
      <table>
        <thead>
          <tr><th>ID</th><th>الأصل</th><th>النوع/الاتجاه</th><th>الحالة</th><th>الدخول</th><th>وقف الخسارة</th><th>الخروج</th></tr>
        </thead>
        <tbody>{''.join(rows) if rows else empty}</tbody>
      </table>
      <div class="hint">فلترة: أضف ?symbol=BTCUSDT&status=OPEN إلى الرابط.</div>
    </div>
  </body>
</html>
"""
    return HTMLResponse(content=html)

@app.get("/")
def root():
    return {"message": "🚀 CapitalGuard API is running"}
# --- END OF FILE ---