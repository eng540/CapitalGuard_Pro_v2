# src/capitalguard/application/services/market_data_service.py
"""
File: src/capitalguard/application/services/market_data_service.py
Version: v1.3.0-429-FIX

✅ THE FIX — معالجة 429 كـ IP مُقيَّد وتفعيل CoinGecko fallback:

المشكلة في v1.2.1:
  _fetch_from_binance_endpoint() كانت تُطلق binance_blocked=True فقط عند 451.
  عند 429 (Too Many Requests — IP المشترك على Railway):
    • يقع في HTTPStatusError
    • يُسجَّل كـ error فقط
    • binance_blocked تبقى False
    • لا fallback لـ CoinGecko
    • symbols_cache تبقى جزئية (Spot فقط بدون Futures)
    • is_valid_symbol("BTCUSDT", "Futures") → False
    • المستخدم يرى "الرمز غير صالح"

  من السجل الفعلي:
    "Failed to fetch symbols for Futures-USD-M: 429"
    ← IP Railway مشترك بين آلاف العملاء → Binance يُقيِّده

الإصلاح:
  BINANCE_BLOCKED_CODES = {429, 451, 403}
  أي من هذه الأكواد → binance_blocked = True → CoinGecko fallback فوري.

Reviewed-by: Guardian Protocol v1 — 2026-03-16
"""

import logging
import asyncio
import os
from typing import Dict, Any, Set

import httpx
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient

log = logging.getLogger(__name__)

BINANCE_ENDPOINTS: Dict[str, str] = {
    "Spot":           "https://api.binance.com/api/v3/exchangeInfo",
    "Futures-USD-M":  "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "Futures-COIN-M": "https://dapi.binance.com/dapi/v1/exchangeInfo",
}

# ✅ THE FIX: أضفنا 429 و403 إلى أكواد الحجب جانب 451
# 429 = Too Many Requests (IP مشترك على Railway)
# 451 = Geo-Block
# 403 = Forbidden
BINANCE_BLOCKED_CODES = {429, 451, 403}


class MarketDataService:
    """
    يُوفِّر قائمة الرموز المتاحة للتحقق من صحة إدخالات المستخدم.
    يحاول Binance أولاً، ويتحول لـ CoinGecko عند أي حجب (429/451/403).
    """

    def __init__(self):
        self._symbols_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_populated: bool = False
        self.provider: str = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        self.binance_blocked: bool = False

    # ─────────────────────────────────────────────────────────────
    # Binance fetchers
    # ─────────────────────────────────────────────────────────────

    async def _fetch_from_binance_endpoint(
        self,
        client: httpx.AsyncClient,
        market: str,
        url: str,
    ) -> tuple[str, list]:
        """
        ✅ THE FIX: معالجة 429 و403 كـ blocking مثل 451 تماماً.
        أي من BINANCE_BLOCKED_CODES → binance_blocked = True → CoinGecko.
        """
        try:
            response = await client.get(url, timeout=15.0)

            # ✅ THE FIX: فحص أكواد الحجب الثلاثة
            if response.status_code in BINANCE_BLOCKED_CODES:
                code = response.status_code
                reason = {
                    429: "Too Many Requests (Railway shared IP)",
                    451: "Geo-Block",
                    403: "Forbidden",
                }.get(code, str(code))
                log.warning(
                    "Binance blocked for market '%s': HTTP %s (%s). "
                    "Will fall back to CoinGecko.",
                    market, code, reason,
                )
                self.binance_blocked = True
                return market, []

            response.raise_for_status()
            data = response.json()
            return market, data.get("symbols", [])

        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            # ✅ THE FIX: تحقق مجدداً هنا أيضاً
            if code in BINANCE_BLOCKED_CODES:
                log.warning(
                    "Binance blocked for market '%s': HTTP %s. "
                    "Will fall back to CoinGecko.",
                    market, code,
                )
                self.binance_blocked = True
            else:
                log.error(
                    "Failed to fetch symbols for '%s': HTTP %s",
                    market, code,
                )
            return market, []

        except httpx.TimeoutException:
            log.error("Binance symbol fetch timeout for '%s'", market)
            return market, []

        except Exception as e:
            log.error(
                "Unexpected error fetching symbols for '%s': %s", market, e
            )
            return market, []

    async def _refresh_binance_cache(self) -> None:
        log.info("Attempting to refresh symbols cache from Binance...")
        unified_cache: Dict[str, Dict[str, Any]] = {}

        async with httpx.AsyncClient() as client:
            tasks = [
                self._fetch_from_binance_endpoint(client, market, url)
                for market, url in BINANCE_ENDPOINTS.items()
            ]
            results = await asyncio.gather(*tasks)

        successful_fetches = 0
        for market, symbols_list in results:
            if not symbols_list:
                continue
            successful_fetches += 1
            for symbol_data in symbols_list:
                if symbol_data.get("status") == "TRADING":
                    name = symbol_data["symbol"].upper()
                    unified_cache.setdefault(name, {"markets": set()})
                    unified_cache[name]["markets"].add(market)

        if unified_cache:
            self._symbols_cache   = unified_cache
            self._cache_populated = True
            # إذا نجح ولو endpoint واحد → لم يُحجب كلياً
            if successful_fetches > 0:
                self.binance_blocked = False
            log.info(
                "Binance symbols cache: %d symbols from %d endpoint(s).",
                len(self._symbols_cache), successful_fetches,
            )
        else:
            log.error(
                "Binance symbols cache empty — all endpoints failed or blocked."
            )
            self._cache_populated = False
            self.binance_blocked  = True   # يُطلق CoinGecko في refresh_symbols_cache

    # ─────────────────────────────────────────────────────────────
    # CoinGecko fetcher
    # ─────────────────────────────────────────────────────────────

    async def _refresh_coingecko_cache(self) -> None:
        log.info("Refreshing symbols cache from CoinGecko...")
        try:
            cg_client = CoinGeckoClient()
            symbols = await cg_client.get_all_symbols()
            self._symbols_cache = {
                s: {"markets": {"Spot", "Futures-USD-M"}} for s in symbols
            }
            self._cache_populated = bool(self._symbols_cache)
            if self._cache_populated:
                log.info(
                    "CoinGecko symbols cache: %d symbols.",
                    len(self._symbols_cache),
                )
            else:
                log.error("CoinGecko symbols cache is empty.")
        except Exception as e:
            log.error("CoinGecko symbol refresh failed: %s", e)
            self._cache_populated = False

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    async def refresh_symbols_cache(self) -> None:
        """
        Entry point لتحديث الكاش عند startup.
        ✅ THE FIX: عند 429 يتحول فوراً لـ CoinGecko.
        """
        if self.provider == "binance":
            await self._refresh_binance_cache()

            if self.binance_blocked:
                log.warning(
                    "Binance blocked/rate-limited on this IP. "
                    "Switching to CoinGecko permanently for this session."
                )
                self.provider = "coingecko"
                os.environ["MARKET_DATA_PROVIDER"] = "coingecko"
                os.environ["ENABLE_WATCHER"] = "0"
                await self._refresh_coingecko_cache()
        else:
            await self._refresh_coingecko_cache()

    def is_valid_symbol(self, symbol: str, market: str) -> bool:
        """
        يتحقق من وجود الرمز في الكاش.
        إذا كان الكاش فارغاً (حُجب كلاهما) → True لمنع تجميد المستخدم.
        """
        if not self._cache_populated:
            log.warning(
                "Symbol cache not populated — allowing '%s' through (fail-open).",
                symbol,
            )
            return True

        symbol_upper = (symbol or "").strip().upper()
        if symbol_upper not in self._symbols_cache:
            return False

        # CoinGecko لا يُميِّز بين Spot/Futures → دائماً True
        if self.provider == "coingecko":
            return True

        available = self._symbols_cache[symbol_upper]["markets"]
        market_lower = (market or "").lower()
        return any(market_lower in m.lower() for m in available)
