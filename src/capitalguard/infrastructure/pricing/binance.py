# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/pricing/binance.py ---
# File: src/capitalguard/infrastructure/pricing/binance.py
# Version: v2.0.0-ASYNC
#
# ✅ THE FIX:
#   requests.get() (blocking sync) → httpx.AsyncClient (async).
#
#   المشكلة في v1.x:
#     price_service كان يستدعي get_price() عبر run_in_executor().
#     بعد جعل get_price() async، ظهر:
#       "coroutines cannot be used with run_in_executor()"
#
#   الإصلاح:
#     get_price() وget_all_prices() async مباشرة.
#     price_service يستدعيهما بـ await — لا run_in_executor.
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

from __future__ import annotations

import logging
from typing import Optional, Dict

import httpx

log = logging.getLogger(__name__)

BINANCE_SPOT_TICKER = "https://api.binance.com/api/v3/ticker/price"
BINANCE_FUT_TICKER  = "https://fapi.binance.com/fapi/v1/ticker/price"

BINANCE_BLOCKED_CODES = {429, 451, 403}


class BinancePricing:

    @staticmethod
    async def get_price(
        symbol: str,
        spot: bool = True,
        timeout: float = 4.0,
    ) -> Optional[float]:
        """✅ ASYNC: جلب سعر رمز واحد."""
        url = BINANCE_SPOT_TICKER if spot else BINANCE_FUT_TICKER
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    url,
                    params={"symbol": symbol.upper()},
                    timeout=timeout,
                )
            if r.status_code in BINANCE_BLOCKED_CODES:
                log.warning("Binance REST blocked for %s (HTTP %s).", symbol, r.status_code)
                return None
            if not r.is_success:
                log.warning("Binance price fetch failed for %s: %s", symbol, r.text[:200])
                return None
            data = r.json()
            price_val = data.get("price")
            if price_val is not None:
                return float(price_val)
            log.warning("Binance price for %s: no 'price' key. Response: %s", symbol, data)
            return None
        except httpx.TimeoutException:
            log.warning("Binance REST timeout for %s", symbol)
            return None
        except httpx.RequestError as e:
            log.error("Binance REST request error for %s: %s", symbol, e)
            return None
        except Exception as e:
            log.warning("Binance REST unexpected error for %s: %s", symbol, e)
            return None

    @staticmethod
    async def get_all_prices(
        spot: bool = True,
        timeout: float = 8.0,
    ) -> Dict[str, float]:
        """✅ ASYNC: جلب أسعار كل الرموز دفعة واحدة."""
        url = BINANCE_SPOT_TICKER if spot else BINANCE_FUT_TICKER
        price_map: Dict[str, float] = {}
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=timeout)
            if r.status_code in BINANCE_BLOCKED_CODES:
                log.warning("Binance REST bulk fetch blocked (HTTP %s).", r.status_code)
                return price_map
            if not r.is_success:
                log.error("Binance bulk price fetch failed: %s", r.text[:200])
                return price_map
            for item in r.json():
                try:
                    sym   = item.get("symbol")
                    price = item.get("price")
                    if sym and price is not None:
                        price_map[sym] = float(price)
                except (ValueError, TypeError):
                    continue
            return price_map
        except httpx.TimeoutException:
            log.warning("Binance REST bulk fetch timeout")
            return price_map
        except httpx.RequestError as e:
            log.error("Binance REST bulk request error: %s", e)
            return price_map
        except Exception as e:
            log.error("Binance REST bulk unexpected error: %s", e, exc_info=True)
            return price_map
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/pricing/binance.py ---
