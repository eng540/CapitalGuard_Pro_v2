# src/capitalguard/infrastructure/pricing/binance.py
"""
File: src/capitalguard/infrastructure/pricing/binance.py
Version: v2.0.0-ASYNC

✅ THE FIX — استبدال requests (blocking) بـ httpx.AsyncClient:

المشكلة:
  requests.get() هي دالة blocking تُجمِّد asyncio event loop أثناء انتظار الرد.
  كان price_service يستدعيها عبر run_in_executor (thread pool) كحلٍّ مؤقت،
  لكن هذا يستهلك threads ويُسبب تأخيراً.

الإصلاح:
  - get_price()      → async مع httpx.AsyncClient
  - get_all_prices() → async مع httpx.AsyncClient
  - حذف run_in_executor من price_service (لم يعد ضرورياً)

ملاحظة حول 429:
  BinancePricing هي الـ LAST RESORT في pipeline الأسعار:
    1. core_cache (WebSocket)  ← الأسرع، صفر REST calls
    2. BinancePricing REST     ← فقط إذا WS لم يُغذِّ بعد
    3. CoinGecko               ← fallback نهائي

Reviewed-by: Guardian Protocol v1 — 2026-03-15
"""
from __future__ import annotations

import logging
from typing import Optional, Dict

import httpx

log = logging.getLogger(__name__)

BINANCE_SPOT_TICKER   = "https://api.binance.com/api/v3/ticker/price"
BINANCE_FUT_TICKER    = "https://fapi.binance.com/fapi/v1/ticker/price"

# أكواد HTTP التي تعني "Binance محجوب/مُقيَّد على هذا الـ IP"
BINANCE_BLOCKED_CODES = {429, 451, 403}


class BinancePricing:
    """
    Async HTTP client لجلب أسعار Binance.
    يُستخدم فقط كـ fallback عندما WebSocket cache فارغ.
    """

    @staticmethod
    async def get_price(
        symbol: str,
        spot: bool = True,
        timeout: float = 4.0,
    ) -> Optional[float]:
        """
        ✅ ASYNC: جلب سعر رمز واحد.
        يُعيد None عند أي فشل (429, 451, خطأ شبكة).
        """
        url = BINANCE_SPOT_TICKER if spot else BINANCE_FUT_TICKER
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    url,
                    params={"symbol": symbol.upper()},
                    timeout=timeout,
                )

            if r.status_code in BINANCE_BLOCKED_CODES:
                log.warning(
                    "Binance REST blocked for %s (HTTP %s) — "
                    "will use WebSocket cache or CoinGecko.",
                    symbol, r.status_code,
                )
                return None

            if not r.is_success:
                log.warning(
                    "Binance single price fetch failed for %s: %s",
                    symbol, r.text[:200],
                )
                return None

            data = r.json()
            price_val = data.get("price")
            if price_val is not None:
                return float(price_val)

            log.warning(
                "Binance price for %s returned OK but no 'price' key: %s",
                symbol, data,
            )
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
        """
        ✅ ASYNC: جلب أسعار كل الرموز في طلب واحد (batch).
        مفيد لتحديث الـ cache دفعةً واحدة بدل طلبات فردية.
        يُعيد dict فارغ عند أي فشل.
        """
        url = BINANCE_SPOT_TICKER if spot else BINANCE_FUT_TICKER
        price_map: Dict[str, float] = {}
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=timeout)

            if r.status_code in BINANCE_BLOCKED_CODES:
                log.warning(
                    "Binance REST bulk fetch blocked (HTTP %s).", r.status_code
                )
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
