# --- START OF FINAL, PRODUCTION-READY FILE (Version 8.1.4.1) ---
# src/capitalguard/application/services/autotrade_service.py

from __future__ import annotations
from dataclasses import dataclass
import os
import logging
import asyncio
from typing import Dict, Any, Optional, Callable

from capitalguard.application.services.risk_service import RiskService, SizingResult
from capitalguard.infrastructure.execution.binance_exec import BinanceExec, OrderResult
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import RecommendationRepository, Recommendation

log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float = 0.0) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _map_side_to_order(side_raw: str) -> str:
    """
    Map domain side values to exchange order sides.
    Accepts variations like LONG/SHORT/BUY/SELL (case-insensitive).
    """
    if not side_raw:
        raise ValueError("Side is required")
    s = side_raw.strip().upper()
    if s in ("LONG", "BUY"):
        return "BUY"
    if s in ("SHORT", "SELL"):
        return "SELL"
    raise ValueError(f"Unsupported side value: {side_raw}")


def _is_limit_order(order_type_raw: str) -> bool:
    return bool(order_type_raw and order_type_raw.strip().upper() == "LIMIT")


async def _run_in_thread(fn: Callable, *args, **kwargs):
    """Utility to run blocking work in a thread and not block the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


@dataclass
class AutoTradeService:
    """
    Handles executing an initial order on Binance according to a Recommendation.
    Behavior:
      - dry-run by default unless AUTO_TRADE_ENABLED=true
      - live trading requires TRADE_LIVE_ENABLED=true
    Notes:
      - This service is async-friendly: blocking DB operations / sync notifier calls are
        executed in a thread pool to avoid blocking the event loop.
      - Side and order_type validation is enforced.
      - After order attempt, repository is updated with an event (ORDER_PLACED or ORDER_FAILED).
    """
    repo: RecommendationRepository
    notifier: Any  # expected to provide send_admin_alert(text: str) (sync or async)
    risk: RiskService
    exec_spot: BinanceExec
    exec_futu: BinanceExec

    async def _creds_ok_async(self, exec_client: BinanceExec) -> bool:
        """Check presence of creds for the selected execution client (async)."""
        # Minimal check: both key and secret exist. Do not ping API here to avoid extra calls.
        try:
            return bool(getattr(exec_client, "creds", None) and exec_client.creds.api_key and exec_client.creds.api_secret)
        except Exception:
            return False

    async def _notify_async(self, text: str):
        """Call notifier in a non-blocking way. Supports sync or async notifier implementations."""
        try:
            if hasattr(self.notifier, "send_admin_alert"):
                maybe = getattr(self.notifier, "send_admin_alert")
                if asyncio.iscoroutinefunction(maybe):
                    await maybe(text)
                else:
                    await _run_in_thread(maybe, text)
            else:
                # fallback to generic call if provided differently
                if asyncio.iscoroutinefunction(self.notifier):
                    await self.notifier(text)
                else:
                    await _run_in_thread(self.notifier, text)
        except Exception:
            log.exception("Autotrade failed to send notification.")

    async def _repo_get_rec(self, rec_id: int) -> Optional[Recommendation]:
        """Fetch recommendation using repo.get(session, id) executed in thread (sync DB)."""
        def _get():
            with SessionLocal() as session:
                # repo expected to have 'get' or 'get_by_id' signature; adapt if needed
                if hasattr(self.repo, "get"):
                    return self.repo.get(session, rec_id)
                if hasattr(self.repo, "get_by_id"):
                    return self.repo.get_by_id(session, rec_id)
                # generic fallback: try attribute 'get'
                return None
        return await _run_in_thread(_get)

    async def _repo_update_event(self, rec: Recommendation, event_type: str, event_data: Dict[str, Any]) -> Optional[Recommendation]:
        """Call repo.update_with_event in a thread (sync DB session inside)."""
        def _update():
            with SessionLocal() as session:
                return self.repo.update_with_event(session, rec, event_type, event_data)
        return await _run_in_thread(_update)

    async def execute_for_rec_async(self, rec_id: int, *, override_risk_pct: Optional[float] = None) -> Dict[str, Any]:
        """
        Execute trade for recommendation id.
        Returns a dict describing outcome, safe for callers to log/return.
        """
        # Fetch recommendation from DB (sync call in thread)
        rec = await self._repo_get_rec(rec_id)

        if not rec:
            log.warning("AutoTrade: recommendation id=%s not found", rec_id)
            return {"ok": False, "msg": "Recommendation not found"}

        # Environment toggles / risk
        auto_en = _env_bool("AUTO_TRADE_ENABLED", False)
        live_en = _env_bool("TRADE_LIVE_ENABLED", False)
        risk_pct = override_risk_pct if override_risk_pct is not None else _env_float("RISK_DEFAULT_PCT", 1.0)

        market = (rec.market or "Futures").strip()
        is_spot = market.lower().startswith("spot")
        exec_client = self.exec_spot if is_spot else self.exec_futu

        # Validate credentials for selected client
        if not await self._creds_ok_async(exec_client):
            msg = "API credentials for selected market are not configured"
            log.error(msg)
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        # Fetch balance from exchange
        try:
            balance = await exec_client.account_balance()
        except Exception as e:
            log.exception("Failed fetching balance for rec=%s: %s", rec_id, e)
            return {"ok": False, "msg": "Failed to fetch account balance"}

        if balance is None or balance <= 0:
            msg = "Could not fetch account balance or balance is zero"
            log.error(msg)
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        # Prepare trade sizing
        try:
            side_raw = getattr(rec.side, "value", rec.side) if hasattr(rec, "side") else getattr(rec, "side", None)
            side_str = side_raw.upper() if isinstance(side_raw, str) else str(side_raw)
            side_order = _map_side_to_order(side_str)
        except Exception as e:
            msg = f"Invalid side in recommendation: {e}"
            log.exception(msg)
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        # Entry/SL extraction and validation
        try:
            entry = getattr(rec.entry, "value", rec.entry)
            sl = getattr(rec.stop_loss, "value", rec.stop_loss)
            symbol = getattr(rec.asset, "value", rec.asset).upper()
            order_type = getattr(rec.order_type, "value", rec.order_type)
            entry = float(entry)
            sl = float(sl)
            if entry <= 0 or sl <= 0:
                raise ValueError("Entry/SL must be positive numbers")
        except Exception as e:
            msg = f"Invalid entry/stop_loss/symbol in recommendation: {e}"
            log.exception(msg)
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        # Compute quantity using RiskService (async)
        try:
            sz: SizingResult = await self.risk.compute_qty_async(
                symbol=symbol, side=side_str, market=market,
                account_usdt=balance, risk_pct=risk_pct, entry=entry, sl=sl
            )
        except ValueError as e:
            msg = f"Risk calculation error: {e}"
            log.error(msg)
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}
        except Exception as e:
            log.exception("Unexpected error in risk calculation for rec=%s: %s", rec_id, e)
            return {"ok": False, "msg": "Risk calculation failed"}

        # Validate computed qty
        if not sz or getattr(sz, "qty", 0) <= 0:
            msg = "Computed quantity is zero or invalid"
            log.error(msg)
            await self._notify_async(f"Autotrade error: {msg} (rec={rec_id})")
            return {"ok": False, "msg": msg}

        # Prepare human-readable summary
        summary = f"{symbol} {side_order} qty={sz.qty:g} @ ~{sz.entry:g} risk={risk_pct}% (min_notional={sz.step_size}, tick={sz.tick_size})"

        # Dry run / Auto-trade toggles
        if not auto_en:
            msg = f"🤖 Dry-Run (Auto-trade disabled): {summary}"
            log.info(msg)
            await self._notify_async(msg)
            return {"ok": True, "dry_run": True, "qty": sz.qty, "entry": sz.entry, "risk_pct": risk_pct}

        if not live_en:
            msg = f"🤖 Auto-Trade (LIVE OFF): {summary}"
            log.info(msg)
            await self._notify_async(msg)
            return {"ok": True, "live": False, "qty": sz.qty, "entry": sz.entry, "risk_pct": risk_pct}

        # Place order (async)
        try:
            res: OrderResult = await exec_client.place_order(
                symbol=symbol,
                side=side_order,
                order_type=order_type.upper() if order_type else "MARKET",
                quantity=sz.qty,
                price=sz.entry if _is_limit_order(order_type) else None,
            )
        except Exception as e:
            log.exception("Order request failed for rec=%s: %s", rec_id, e)
            # record failure event in repo (best-effort)
            await self._repo_update_event(rec, "ORDER_FAILED", {"error": str(e)})
            await self._notify_async(f"❌ Order Request Exception: {e} (rec={rec_id})")
            return {"ok": False, "msg": "Order request failed", "error": str(e)}

        # Process response
        if res.ok:
            # Best-effort: record ORDER_PLACED event with payload
            try:
                event_data = {"payload": res.payload, "qty": sz.qty, "entry": sz.entry}
                await self._repo_update_event(rec, "ORDER_PLACED", event_data)
            except Exception:
                log.exception("Failed to record ORDER_PLACED event for rec=%s", rec_id)
            msg = f"✅ Order Placed: {summary}"
            log.info(msg)
            await self._notify_async(msg)
            return {"ok": True, "live": True, "payload": res.payload}
        else:
            # record ORDER_FAILED with Binance message
            try:
                event_data = {"message": res.message, "payload": res.payload}
                await self._repo_update_event(rec, "ORDER_FAILED", event_data)
            except Exception:
                log.exception("Failed to record ORDER_FAILED event for rec=%s", rec_id)
            msg = f"❌ Order Failed: {symbol} — {res.message[:160]}"
            log.error(msg)
            await self._notify_async(msg)
            return {"ok": False, "msg": res.message, "payload": res.payload}

    # synchronous convenience method for compatibility
    def execute_for_rec(self, *args, **kwargs):
        """Run the async execute_for_rec_async from sync code (blocking)."""
        return asyncio.get_event_loop().run_until_complete(self.execute_for_rec_async(*args, **kwargs))

# --- END OF FINAL, PRODUCTION-READY FILE (Version 8.1.4.1) ---