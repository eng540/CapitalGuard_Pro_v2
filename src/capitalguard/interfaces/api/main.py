#--- START OF FILE: src/capitalguard/interfaces/api/main.py --- import logging from fastapi import FastAPI, HTTPException, Depends, Request from fastapi.responses import JSONResponse, HTMLResponse from telegram import Update from telegram.ext import Application, PicklePersistence

from capitalguard.config import settings from capitalguard.boot import build_services from capitalguard.interfaces.api.deps import require_api_key from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn from capitalguard.interfaces.telegram.handlers import register_all_handlers

app = FastAPI(title="CapitalGuard Pro API", version="4.0.0")

نبني الخدمات مرة واحدة (Composition Root)

_services_pack: dict = build_services()

نضعها في state ليستفيد منها الـ API

app.state.services = _services_pack

--- Telegram Webhook Setup (نفس الخدمات) ---

ptb_app: Application | None = None

def _build_ptb_app() -> Application: """ إنشاء تطبيق تيليجرام مرة واحدة، وحقن نفس الخدمات في bot_data، ثم تسجيل جميع الـ handlers بنمط الحقن الصريح للأوامر. """ persistence = PicklePersistence(filepath="./telegram_bot_persistence") application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()

# حقن نفس الخدمات في bot_data
application.bot_data.update(_services_pack)

# تسجيل جميع الـ handlers (الأوامر عبر partial، المحادثات/Callbacks عبر bot_data)
register_all_handlers(application, _services_pack)
return application

if settings.TELEGRAM_BOT_TOKEN: ptb_app = _build_ptb_app()

@app.on_event("startup")
async def on_startup():
    # تهيئة PTB
    await ptb_app.initialize()

    # IMPORTANT: إعادة الحقن بعد initialize() لأن PicklePersistence قد يكتب bot_data
    ptb_app.bot_data.update(_services_pack)
    logging.info("Re-injected services into bot_data after initialize().")

    # إعداد Webhook إن كان مضبوطًا
    if settings.TELEGRAM_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        logging.info("Telegram webhook set to: %s", settings.TELEGRAM_WEBHOOK_URL)

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

else: logging.warning("TELEGRAM_BOT_TOKEN not set; Telegram features disabled.")

--- REST API: تستخدم نفس الخدمات من app.state.services ---

@app.get( "/recommendations", response_model=list[RecommendationOut], dependencies=[Depends(require_api_key)], ) def list_recs(request: Request, channel_id: int | None = None): trade = request.app.state.services["trade_service"] items = trade.list_all(channel_id) return [RecommendationOut.model_validate(i) for i in items]

@app.post( "/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_api_key)], ) def close_rec(request: Request, rec_id: int, payload: CloseIn): trade = request.app.state.services["trade_service"] try: rec = trade.close(rec_id, payload.exit_price) return RecommendationOut.model_validate(rec) except ValueError as e: raise HTTPException(status_code=404, detail=str(e))

--- Dashboard قراءة فقط (تحسين UX بدون تغييرات أمنية) ---

@app.get("/dashboard", response_class=HTMLResponse) def dashboard(request: Request): trade = request.app.state.services["trade_service"] items = trade.list_all()

rows = []
for r in items:
    rid   = getattr(r, "id", "")
    asset = getattr(r, "asset", "")
    side  = getattr(r, "side", "")
    status= getattr(r, "status", "")
    entry = getattr(r, "entry", "")
    sl    = getattr(r, "stop_loss", "")
    exitp = getattr(r, "exit_price", "") or "-"

    rows.append(
        f"<tr>"
        f"<td>#{rid}</td><td>{asset}</td><td>{side}</td>"
        f"<td class='{status}'>{status}</td>"
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
          <tr><th>ID</th><th>الأصل</th><th>الاتجاه</th><th>الحالة</th><th>الدخول</th><th>وقف الخسارة</th><th>الخروج</th></tr>
        </thead>
        <tbody>{''.join(rows) if rows else empty}</tbody>
      </table>
      <div class="hint">هذه لوحة قراءة فقط — الإدارة تتم عبر البوت أو واجهات REST.</div>
    </div>
  </body>
</html>
"""
return HTMLResponse(content=html)

@app.get("/") def root(): return {"message": "🚀 CapitalGuard API is running"} 
#--- END OF FILE ---

