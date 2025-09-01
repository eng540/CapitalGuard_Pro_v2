from __future__ import annotations
import logging, csv, io, json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, Request, Query, Header
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from telegram import Update
from telegram.ext import Application, PicklePersistence

from capitalguard.config import settings
from capitalguard.boot import build_services
from capitalguard.interfaces.api.deps import require_api_key, get_current_user, is_admin, ping_db
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn
from capitalguard.interfaces.telegram.handlers import register_all_handlers

log = logging.getLogger(__name__)
app = FastAPI(title="CapitalGuard Pro API", version="5.0.1")

_services_pack: dict = build_services()
app.state.services = _services_pack

ptb_app: Application | None = None

@app.on_event("startup")
async def on_startup():
    global ptb_app
    if not settings.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set; bot disabled.")
        return

    # آمن حتى لو الإعداد غير موجود
    state_file = getattr(settings, "TELEGRAM_STATE_FILE", None)
    persistence = PicklePersistence(filepath=state_file) if state_file else None

    ptb_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    ptb_app.bot_data["services"] = _services_pack
    register_all_handlers(ptb_app, services=_services_pack)

    # جدولة التنبيهات: فقط إن كان job_queue متاحاً
    try:
        _services_pack["alert_service"].schedule_job(ptb_app, interval_sec=30)
    except Exception as e:
        log.warning("Alert schedule failed: %s", e)

    await ptb_app.initialize()
    await ptb_app.start()

    if not getattr(settings, "TELEGRAM_WEBHOOK_URL", None):
        log.error("TELEGRAM_WEBHOOK_URL not set; webhook mode requires a public HTTPS URL.")
        return

    secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", None)
    await ptb_app.bot.set_webhook(url=settings.TELEGRAM_WEBHOOK_URL, secret_token=secret)
    log.info("Telegram bot started (webhook-only) -> %s", settings.TELEGRAM_WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    global ptb_app
    try:
        if ptb_app:
            try:
                await ptb_app.bot.delete_webhook()
            except Exception:
                pass
            await ptb_app.stop()
    except Exception:
        pass

# ========= Telegram Webhook Routes (المساران معًا) =========
async def _process_telegram_update(request: Request, header_secret: str | None):
    secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", None)
    if secret and header_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    if not ptb_app:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    update = Update.de_json(payload, ptb_app.bot)
    await ptb_app.process_update(update)
    return {"ok": True}

@app.post("/telegram/webhook")
async def telegram_webhook_a(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    return await _process_telegram_update(request, x_telegram_bot_api_secret_token)

@app.post("/webhook/telegram")
async def telegram_webhook_b(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    return await _process_telegram_update(request, x_telegram_bot_api_secret_token)

# ---------------- Health ----------------
@app.get("/healthz")
def healthz():
    return {"db": ping_db(), "version": app.version, "env": settings.ENV}

# ------------- Recommendations -----------
@app.get("/recommendations", response_model=list[RecommendationOut], dependencies=[Depends(require_api_key)])
def list_recommendations(
    user = Depends(get_current_user),
    symbol: str | None = Query(default=None),
    status: str | None = Query(default=None),
    market: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    all: bool = Query(default=False),
):
    analytics = _services_pack["analytics_service"]
    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None
    items = analytics.list_filtered(
        user_id=None if (all and is_admin(user)) else user.id,
        symbol=symbol, status=status, market=market, date_from=df, date_to=dt
    )
    return [RecommendationOut.model_validate(i) for i in items]

@app.post("/recommendations/{rec_id}/close", response_model=RecommendationOut, dependencies=[Depends(require_api_key)])
def close_recommendation(rec_id: int, payload: CloseIn):
    trade = _services_pack["trade_service"]
    try:
        rec = trade.close(rec_id, payload.exit_price)
        return RecommendationOut.model_validate(rec)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# ---------------- Dashboard --------------
@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_api_key)])
def dashboard(
    user = Depends(get_current_user),
    symbol: str | None = Query(default=None),
    status: str | None = Query(default=None),
    market: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    all: bool = Query(default=False),
):
    analytics = _services_pack["analytics_service"]
    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None
    items = analytics.list_filtered(
        user_id=None if (all and is_admin(user)) else user.id,
        symbol=symbol, status=status, market=market, date_from=df, date_to=dt
    )
    rows = "".join(
        f"<tr><td>{r.id}</td><td>{getattr(r.asset,'value',r.asset)}</td><td>{getattr(r.side,'value',r.side)}</td>"
        f"<td>{r.status}</td><td>{float(getattr(r.entry,'value',r.entry)):g}</td>"
        f"<td>{float(getattr(r.stop_loss,'value',r.stop_loss)):g}</td>"
        f"<td>{'' if r.exit_price is None else float(r.exit_price):g}</td>"
        f"<td>{'' if r.closed_at is None else r.closed_at.strftime('%Y-%m-%d')}</td></tr>"
        for r in items
    )
    curve = _services_pack["analytics_service"].pnl_curve(items)
    win = _services_pack["analytics_service"].win_rate(items)
    by_market = _services_pack["analytics_service"].summary_by_market(items)
    html = f"""
<!doctype html><html><head><meta charset="utf-8"><title>CapitalGuard Dashboard</title>
<style>body{{font-family:system-ui,Arial;padding:20px}} table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ddd;padding:6px}} th{{background:#f7f7f7}}</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head><body>
<h2>📊 CapitalGuard — Dashboard</h2>
<form method="get" action="/dashboard">
  Symbol <input name="symbol" value="{symbol or ''}">
  Status <input name="status" value="{status or ''}">
  Market <input name="market" value="{market or ''}">
  From <input type="date" name="date_from" value="{(date_from or '')}">
  To <input type="date" name="date_to" value="{(date_to or '')}">
  All <input type="checkbox" name="all" {"checked" if all else ""}>
  <button type="submit">Filter</button>
</form>

<h3>Summary</h3>
<div>Win Rate: <b>{win:.2f}%</b></div>
<pre>{json.dumps(by_market, indent=2)}</pre>

<canvas id="pnlCurve"></canvas>
<script>
const curve = {json.dumps(curve)};
new Chart(document.getElementById('pnlCurve'), {{
  type: 'line',
  data: {{
    labels: curve.map(x => x[0]),
    datasets: [{{label: 'Cumulative PnL %', data: curve.map(x => x[1])}}]
  }}
}});
</script>

<h3>Recommendations</h3>
<table>
<thead><tr><th>ID</th><th>Asset</th><th>Side</th><th>Status</th><th>Entry</th><th>SL</th><th>Exit</th><th>Closed At</th></tr></thead>
<tbody>{rows or '<tr><td colspan="8">No data</td></tr>'}</tbody>
</table>
</body></html>
"""
    return HTMLResponse(content=html)

# ---------------- Report -----------------
@app.get("/report", dependencies=[Depends(require_api_key)])
def report(
    user = Depends(get_current_user),
    symbol: str | None = Query(default=None),
    status: str | None = Query(default=None),
    market: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    all: bool = Query(default=False),
    format: str = Query(default="csv"),
):
    analytics = _services_pack["analytics_service"]
    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None
    items = analytics.list_filtered(
        user_id=None if (all and is_admin(user)) else user.id,
        symbol=symbol, status=status, market=market, date_from=df, date_to=dt
    )

    def row(r):
        entry = float(getattr(r.entry, "value", r.entry))
        sl    = float(getattr(r.stop_loss, "value", r.stop_loss))
        exitp = float(getattr(r, "exit_price", 0) or 0)
        side  = str(getattr(r.side, "value", r.side))
        pnl   = analytics._pnl_percent(side, entry, exitp) if (r.exit_price is not None) else None
        rr    = analytics.rr_actual(r)
        return dict(
            id=r.id, asset=str(getattr(r.asset, "value", r.asset)), side=side, status=str(r.status),
            entry=entry, stop_loss=sl, exit_price=r.exit_price, created_at=r.created_at, closed_at=r.closed_at,
            pnl_percent=pnl, rr_actual=rr
        )

    rows = [row(r) for r in items]

    if format == "html":
        html_buf = io.StringIO()
        html_buf.write("<!doctype html><html><head><meta charset='utf-8'><title>Report</title></head><body>")
        html_buf.write("<h3>Report</h3>")
        html_buf.write("<table border='1' cellpadding='6'>")
        html_buf.write("<tr><th>ID</th><th>Asset</th><th>Side</th><th>Status</th><th>Entry</th><th>SL</th><th>Exit</th><th>PnL%</th><th>R/R act</th></tr>")
        if not rows:
            html_buf.write("<tr><td colspan='9'>No data</td></tr>")
        else:
            for x in rows:
                pnl_str  = "" if x["pnl_percent"] is None else f"{x['pnl_percent']:.2f}%"
                rr_str   = "" if x["rr_actual"]   is None else f"{x['rr_actual']:.2f}"
                exit_str = "" if x["exit_price"]  is None else f"{float(x['exit_price']):g}"
                html_buf.write(
                    f"<tr><td>{x['id']}</td><td>{x['asset']}</td><td>{x['side']}</td><td>{x['status']}</td>"
                    f"<td>{x['entry']:g}</td><td>{x['stop_loss']:g}</td><td>{exit_str}</td>"
                    f"<td>{pnl_str}</td><td>{rr_str}</td></tr>"
                )
        html_buf.write("</table></body></html>")
        return HTMLResponse(content=html_buf.getvalue())

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID","Asset","Side","Status","Entry","SL","Exit","PnL%","RR_actual","Created","Closed"])
    for x in rows:
        writer.writerow([
            x["id"], x["asset"], x["side"], x["status"], f"{x['entry']:g}", f"{x['stop_loss']:g}",
            "" if x["exit_price"] is None else f"{float(x['exit_price']):g}",
            "" if x["pnl_percent"] is None else f"{x['pnl_percent']:.2f}",
            "" if x["rr_actual"]   is None else f"{x['rr_actual']:.2f}",
            x["created_at"].isoformat() if x["created_at"] else "",
            x["closed_at"].isoformat()  if x["closed_at"]  else "",
        ])
    data = buf.getvalue().encode("utf-8")
    headers = {"Content-Disposition": "attachment; filename=report.csv"}
    return StreamingResponse(io.BytesIO(data), media_type="text/csv", headers=headers)

# --------------- Risk / Auto ---------------
@app.get("/risk/size", dependencies=[Depends(require_api_key)])
def risk_size(symbol: str, side: str, market: str, entry: float, sl: float, x_risk_pct: float | None = Header(default=None)):
    risk_pct = x_risk_pct if x_risk_pct is not None else 1.0
    risk = _services_pack["risk_service"]
    ex = _services_pack["autotrade_service"].exec_spot if market.lower().startswith("spot") else _services_pack["autotrade_service"].exec_futu
    bal = ex.account_balance() or 0.0
    if bal <= 0:
        raise HTTPException(status_code=400, detail="No balance or credentials")
    res = risk.compute_qty(symbol=symbol, side=side, market=market, account_usdt=bal, risk_pct=risk_pct, entry=entry, sl=sl)
    return {"qty": res.qty, "notional": res.notional, "risk_usdt": res.risk_usdt, "step_size": res.step_size, "tick_size": res.tick_size, "entry": res.entry}

@app.post("/autotrade/execute/{rec_id}", dependencies=[Depends(require_api_key)])
def autotrade_execute(rec_id: int, risk_pct: float | None = Query(default=None), order_type: str = Query(default="MARKET")):
    at = _services_pack["autotrade_service"]
    out = at.execute_for_rec(rec_id, override_risk_pct=risk_pct, order_type=order_type)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("msg", "failed"))
    return out

@app.post("/webhook/tradingview", dependencies=[Depends(require_api_key)])
async def tv_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    symbol = (data.get("symbol") or data.get("SYMBOL") or "").upper()
    side   = (data.get("side") or data.get("SIDE") or "").upper()
    market = (data.get("market") or data.get("MARKET") or "Futures").title()
    entry  = float(data.get("entry") or data.get("ENTRY") or 0)
    sl     = float(data.get("sl") or data.get("SL") or 0)
    if symbol and side and entry and sl:
        at = _services_pack["autotrade_service"]
        out = at.execute_for_rec(rec_id = data.get("rec_id") or 0, override_risk_pct = None, order_type = "MARKET")
        return JSONResponse({"ok": True, "executed": out})
    return JSONResponse({"ok": True, "note": "Draft only"})

@app.get("/")
def root():
    return {"message": "🚀 CapitalGuard API is running", "version": app.version}