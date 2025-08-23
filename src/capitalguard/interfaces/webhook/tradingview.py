from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from typing import Any, List
from capitalguard.interfaces.api.deps import require_api_key
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService

repo = RecommendationRepository()
notifier = TelegramNotifier()
trade = TradeService(repo, notifier)

router = APIRouter(tags=["webhook"])

@router.post("/webhook/tradingview")
async def tradingview_webhook(payload: dict, request: Request, _=Depends(require_api_key)):
    try:
        symbol = (payload.get("symbol") or payload.get("asset") or "").upper()
        side = (payload.get("side") or "").upper()
        entry = float(payload.get("entry"))
        sl = float(payload.get("sl") or payload.get("stop_loss"))
        tg = payload.get("targets") or payload.get("tps") or []
        if isinstance(tg, str):
            targets: List[float] = [float(x) for x in tg.replace(" ", "").split(",") if x]
        else:
            targets = [float(x) for x in tg]
        rec = trade.create(symbol, side, entry, sl, targets)
        return {"ok": True, "id": rec.id}
    except Exception as e:
        return {"ok": False, "error": str(e)}