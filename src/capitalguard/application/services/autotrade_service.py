# --- START OF FINAL, REBUILT, AND ARCHITECTURALLY-CORRECT FILE (Version 12.0.0) ---
# src/capitalguard/application/services/autotrade_service.py

from __future__ import annotations
from dataclasses import dataclass
import os
import logging
import asyncio
from typing import Dict, Any, Optional, Callable

from sqlalchemy.orm import Session
from capitalguard.application.services.risk_service import RiskService, SizingResult
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, OrderResult
from capitalguard.infrastructure.db.repository import RecommendationRepository, Recommendation

log = logging.getLogger(__name__)

# Helper functions remain the same
def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else default

def _env_float(name: str, default: float = 0.0) -> float:
    v = os.getenv(name)
    try: return float(v) if v is not None else default
    except (ValueError, TypeError): return default

def _map_side_to_order(side_raw: str) -> str:
    s = (side_raw or "").strip().upper()
    if s in ("LONG", "BUY"): return "BUY"
    if s in ("SHORT", "SELL"): return "SELL"
    raise ValueError(f"Unsupported side value: {side_raw}")

def _is_limit_order(order_type_raw: str) -> bool:
    return bool(order_type_raw and order_type_raw.strip().upper() == "LIMIT")

@dataclass
class AutoTradeService:
    """
    Handles executing orders on Binance.
    ✅ ARCHITECTURAL FIX: All database operations now use an injected Session object.
    """
    repo: RecommendationRepository
    notifier: Any
    risk: RiskService
    exec_spot: BinanceExec
    exec_futu: BinanceExec

    async def _creds_ok_async(self, exec_client: BinanceExec) -> bool:
        try:
            return bool(getattr(exec_client, "creds", None) and exec_client.creds.api_key and exec_client.creds.api_secret)
        except Exception:
            return False

    async def _notify_async(self, text: str):
        try:
            if hasattr(self.notifier, "send_admin_alert"):
                maybe_awaitable = self.notifier.send_admin_alert(text)
                if asyncio.iscoroutine(maybe_awaitable):
                    await maybe_awaitable
        except Exception:
            log.exception("Autotrade failed to send notification.")

    # ✅ UoW FIX: The execute method now requires a session.
    async def execute_for_rec_async(self, session: Session, rec_id: int, *, override_risk_pct: Optional[float] = None) -> Dict[str, Any]:
        """
        Execute trade for a recommendation ID using the provided database session.
        """
        rec = self.repo.get(session, rec_id)
        if not rec:
            log.warning("AutoTrade: recommendation id=%s not found", rec_id)
            return {"ok": False, "msg": "Recommendation not found"}

        auto_en = _env_bool("AUTO_TRADE_ENABLED", False)
        live_en = _env_bool("TRADE_LIVE_ENABLED", False)
        risk_pct = override_risk_pct if override_risk_pct is not None else _env_float("RISK_DEFAULT_PCT", 1.0)

        market = (rec.market or "Futures").strip()
        is_spot = market.lower().startswith("spot")
        exec_client = self.exec_spot if is_spot else self.exec_futu

        if not await self._creds_ok_async(exec_client):
            msg = "API credentials for selected market are not configured"
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        try:
            balance = await exec_client.account_balance()
        except Exception as e:
            log.exception("Failed fetching balance for rec=%s: %s", rec_id, e)
            return {"ok": False, "msg": "Failed to fetch account balance"}

        if balance is None or balance <= 0:
            msg = "Could not fetch account balance or balance is zero"
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        try:
            side_str = rec.side.value.upper()
            side_order = _map_side_to_order(side_str)
            entry = float(rec.entry.value)
            sl = float(rec.stop_loss.value)
            symbol = rec.asset.value.upper()
            order_type = rec.order_type.value
            if entry <= 0 or sl <= 0: raise ValueError("Entry/SL must be positive")
        except Exception as e:
            msg = f"Invalid data in recommendation: {e}"
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        try:
            sz: SizingResult = await self.risk.compute_qty_async(
                symbol=symbol, side=side_str, market=market,
                account_usdt=balance, risk_pct=risk_pct, entry=entry, sl=sl
            )
        except ValueError as e:
            msg = f"Risk calculation error: {e}"
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        if not sz or sz.qty <= 0:
            msg = "Computed quantity is zero or invalid"
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        summary = f"{symbol} {side_order} qty={sz.qty:g} @ ~{sz.entry:g} risk={risk_pct}%"

        if not auto_en or not live_en:
            run_mode = "Dry-Run (Auto-trade disabled)" if not auto_en else "Auto-Trade (LIVE OFF)"
            msg = f"🤖 {run_mode}: {summary}"
            log.info(msg)
            await self._notify_async(msg)
            return {"ok": True, "dry_run": not live_en, "live": False, "qty": sz.qty, "entry": sz.entry}

        try:
            res: OrderResult = await exec_client.place_order(
                symbol=symbol, side=side_order, order_type=order_type.upper(),
                quantity=sz.qty, price=sz.entry if _is_limit_order(order_type) else None,
            )
        except Exception as e:
            log.exception("Order request failed for rec=%s: %s", rec_id, e)
            self.repo.update_with_event(session, rec, "ORDER_FAILED", {"error": str(e)})
            await self._notify_async(f"❌ Order Request Exception: {e} (rec={rec_id})")
            return {"ok": False, "msg": "Order request failed", "error": str(e)}

        if res.ok:
            event_data = {"payload": res.payload, "qty": sz.qty, "entry": sz.entry}
            self.repo.update_with_event(session, rec, "ORDER_PLACED", event_data)
            msg = f"✅ Order Placed: {summary}"
            log.info(msg)
            await self._notify_async(msg)
            return {"ok": True, "live": True, "payload": res.payload}
        else:
            event_data = {"message": res.message, "payload": res.payload}
            self.repo.update_with_event(session, rec, "ORDER_FAILED", event_data)
            msg = f"❌ Order Failed: {symbol} — {res.message[:160]}"
            log.error(msg)
            await self._notify_async(msg)
            return {"ok": False, "msg": res.message, "payload": res.payload}

# --- END OF FINAL, REBUILT, AND ARCHITECTURALLY-CORRECT FILE ---