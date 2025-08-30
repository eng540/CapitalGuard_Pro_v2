# --- START OF FILE: src/capitalguard/application/services/autotrade_service.py ---
from __future__ import annotations
from dataclasses import dataclass
import os

from capitalguard.application.services.risk_service import RiskService
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, OrderResult

def _env_bool(name: str, default: bool=False) -> bool:
    v = os.getenv(name)
    if v is None: return default
    return str(v).strip().lower() in ("1","true","yes","on")

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except Exception:
        return default

@dataclass
class AutoTradeService:
    """تنفيذ أمر أولي على Binance اعتمادًا على توصية (Dry-Run افتراضي)."""
    repo: any
    notifier: any
    risk: RiskService
    exec_spot: BinanceExec
    exec_futu: BinanceExec

    def _creds_ok(self) -> bool:
        return bool(self.exec_spot.creds.api_key and self.exec_spot.creds.api_secret)

    def execute_for_rec(self, rec_id: int, *, override_risk_pct: float | None = None, order_type: str = "MARKET") -> dict:
        rec = self.repo.get(rec_id)
        if not rec:
            return {"ok": False, "msg": "Recommendation not found"}

        auto_en   = _env_bool("AUTO_TRADE_ENABLED", False)
        live_en   = _env_bool("TRADE_LIVE_ENABLED", False)
        risk_pct  = override_risk_pct if override_risk_pct is not None else _env_float("RISK_DEFAULT_PCT", 1.0)

        market = getattr(rec, "market", "Spot")
        is_spot = str(getattr(market, "value", market)).lower().startswith("spot")
        ex = self.exec_spot if is_spot else self.exec_futu

        if not self._creds_ok():
            return {"ok": False, "msg": "No API credentials"}

        balance = ex.account_balance() or 0.0
        if balance <= 0:
            return {"ok": False, "msg": "No balance"}

        side  = str(getattr(rec.side, "value", rec.side)).upper()
        entry = float(getattr(rec.entry, "value", rec.entry))
        sl    = float(getattr(rec.stop_loss, "value", rec.stop_loss))
        symbol = str(getattr(rec.asset, "value", rec.asset)).upper()

        sz = self.risk.compute_qty(symbol=symbol, side=side, market=("Spot" if is_spot else "Futures"),
                                   account_usdt=balance, risk_pct=risk_pct, entry=entry, sl=sl)
        side_order = "BUY" if side == "LONG" else "SELL"

        if not auto_en:
            self._notify(f"🤖 Dry-Run (auto disabled): {symbol} {side_order} qty={sz.qty:g} @ ~{sz.entry:g} risk {risk_pct}%")
            return {"ok": True, "dry_run": True, "qty": sz.qty, "entry": sz.entry, "risk_pct": risk_pct}

        if not live_en:
            self._notify(f"🤖 Auto-Trade (LIVE OFF): {symbol} {side_order} qty={sz.qty:g} @ ~{sz.entry:g} risk {risk_pct}%")
            return {"ok": True, "live": False, "qty": sz.qty, "entry": sz.entry, "risk_pct": risk_pct}

        res: OrderResult = ex.place_order(symbol=symbol, side=side_order, order_type=order_type.upper(), quantity=sz.qty,
                                          price=None if order_type.upper()=="MARKET" else sz.entry)
        if res.ok:
            self._notify(f"✅ Order Placed: {symbol} {side_order} qty={sz.qty:g} ({order_type.upper()})")
            return {"ok": True, "live": True, "payload": res.payload}
        else:
            self._notify(f"❌ Order Failed: {symbol} — {res.message[:160]}")
            return {"ok": False, "msg": res.message}

    def _notify(self, text: str):
        try:
            self.notifier._post("sendMessage", {
                "chat_id": int(self.notifier.settings.TELEGRAM_CHAT_ID),
                "text": text
            })
        except Exception:
            pass
# --- END OF FILE ---