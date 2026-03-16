#--- START OF FINAL, HARDENED, AND PRODUCTION-READY FILE (Version 1.4.0) ---
# src/capitalguard/application/services/market_data_service.py
#
# ✅ THE FIX (v1.4.0 — Circuit Breaker with Auto Recovery):
#
#   v1.3.0 أضاف: {429, 451, 403} → CoinGecko fallback فوري.
#   v1.4.0 يُضيف: Circuit Breaker كامل مع إعادة المحاولة التلقائية.
#
#   المشكلة في v1.3.0:
#     عند 429 كان النظام يتحول لـ CoinGecko نهائياً طوال الجلسة.
#     binance_blocked = True لكن لا شيء يُعيد ضبطها.
#
#   الحل — Circuit Breaker:
#     OPEN:      429 → CoinGecko + timer (30 دقيقة)
#     HALF-OPEN: بعد 30 دقيقة → إعادة محاولة Binance
#     CLOSED:    نجاح → Binance يعود كمصدر رئيسي
#
#   الفرق عن تغيير provider:
#     provider = إعداد Configuration (لا يتغير أثناء التشغيل)
#     binance_blocked = حالة تشغيل Runtime (يتغير تلقائياً)
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-16

import logging
import asyncio
import os
import time
from typing import Dict, Any, Set

import httpx
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient

log = logging.getLogger(__name__)

BINANCE_ENDPOINTS = {
    "Spot": "https://api.binance.com/api/v3/exchangeInfo",
    "Futures-USD-M": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "Futures-COIN-M": "https://dapi.binance.com/dapi/v1/exchangeInfo",
}

# 429 = Too Many Requests (Railway shared IP)
# 451 = Geo-Block
# 403 = Forbidden
BINANCE_BLOCKED_CODES = {429, 451, 403}


class MarketDataService:
    """
    Smart data provider with Circuit Breaker pattern.

    Circuit Breaker states:
      CLOSED    → Binance يعمل (binance_blocked=False)
      OPEN      → Binance محجوب، CoinGecko نشط (binance_blocked=True)
      HALF-OPEN → بعد cooldown، يُحاول Binance مجدداً

    لا يتغير self.provider أثناء التشغيل.
    التحكم الكامل عبر self.binance_blocked فقط.
    """

    def __init__(self):
        self._symbols_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_populated = False
        self.provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()

        # ── Circuit Breaker state ──────────────────────────────
        self.binance_blocked = False
        self.binance_retry_after = 0.0
        self.retry_delay_seconds = int(
            os.getenv("BINANCE_RETRY_DELAY", "1800")  # 30 دقيقة افتراضياً
        )

    # ─────────────────────────────────────────────────────────────
    # Binance fetchers
    # ─────────────────────────────────────────────────────────────

    async def _fetch_from_binance_endpoint(
        self, client: httpx.AsyncClient, market: str, url: str
    ) -> tuple[str, list]:
        """Fetches symbols from a single Binance endpoint."""
        try:
            response = await client.get(url, timeout=15.0)

            if response.status_code in BINANCE_BLOCKED_CODES:
                reason = {
                    429: "Too Many Requests (shared IP)",
                    451: "Geo-Block",
                    403: "Forbidden",
                }.get(response.status_code, str(response.status_code))
                log.warning(
                    f"Binance blocked for {market} "
                    f"(HTTP {response.status_code} — {reason}). "
                    f"Activating Circuit Breaker. "
                    f"Retrying in {self.retry_delay_seconds}s."
                )
                self.binance_blocked = True
                self.binance_retry_after = time.time() + self.retry_delay_seconds
                return market, []

            response.raise_for_status()
            data = response.json()
            return market, data.get("symbols", [])

        except httpx.HTTPStatusError as e:
            if e.response.status_code in BINANCE_BLOCKED_CODES:
                log.warning(
                    f"Binance blocked for {market} "
                    f"(HTTP {e.response.status_code}). "
                    f"Activating Circuit Breaker."
                )
                self.binance_blocked = True
                self.binance_retry_after = time.time() + self.retry_delay_seconds
            else:
                log.error(
                    f"Failed to fetch symbols for {market}: "
                    f"{e.response.status_code}"
                )
            return market, []

        except Exception as e:
            log.error(
                f"Unexpected error fetching symbols for {market}: {e}"
            )
            return market, []

    async def _refresh_binance_cache(self):
        """Fetches and consolidates symbols from all Binance endpoints."""
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
                    symbol_name = symbol_data["symbol"].upper()
                    if symbol_name not in unified_cache:
                        unified_cache[symbol_name] = {"markets": set()}
                    unified_cache[symbol_name]["markets"].add(market)

        if unified_cache:
            self._symbols_cache = unified_cache
            self._cache_populated = True
            log.info(
                f"Binance symbols cache: {len(self._symbols_cache)} symbols "
                f"from {successful_fetches} endpoint(s)."
            )
            if successful_fetches > 0:
                self.binance_blocked = False   # Circuit Breaker → CLOSED
        else:
            log.error(
                "Binance symbols cache empty — all endpoints failed or blocked."
            )
            self._cache_populated = False
            self.binance_blocked = True
            self.binance_retry_after = time.time() + self.retry_delay_seconds

    # ─────────────────────────────────────────────────────────────
    # CoinGecko fetcher
    # ─────────────────────────────────────────────────────────────

    async def _refresh_coingecko_cache(self):
        """Fetches and constructs a symbol list from CoinGecko."""
        log.info("Refreshing symbols cache from CoinGecko...")
        cg_client = CoinGeckoClient()
        symbols = await cg_client.get_all_symbols()
        self._symbols_cache = {
            s: {"markets": {"Spot", "Futures-USD-M"}} for s in symbols
        }
        self._cache_populated = bool(self._symbols_cache)
        if self._cache_populated:
            log.info(
                f"CoinGecko symbols cache: {len(self._symbols_cache)} symbols."
            )
        else:
            log.error("CoinGecko symbols cache is empty.")

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    async def refresh_symbols_cache(self) -> None:
        """
        Entry point لتحديث الكاش.
        يفحص Circuit Breaker قبل محاولة Binance.
        """
        if self.provider != "binance":
            await self._refresh_coingecko_cache()
            return

        # ── Circuit Breaker check ──────────────────────────────
        if self.binance_blocked:
            current_time = time.time()

            if current_time < self.binance_retry_after:
                remaining = int(self.binance_retry_after - current_time)
                log.info(
                    f"Binance cooldown active — using CoinGecko. "
                    f"Retry in {remaining}s."
                )
                await self._refresh_coingecko_cache()
                return

            # Cooldown انتهى → HALF-OPEN
            log.info("Binance cooldown finished — retrying Binance.")
            self.binance_blocked = False

        # ── Try Binance ────────────────────────────────────────
        await self._refresh_binance_cache()

        # إذا فشل Binance مجدداً → CoinGecko
        if self.binance_blocked:
            log.warning(
                "Binance still blocked after retry. "
                f"Switching to CoinGecko. "
                f"Next retry in {self.retry_delay_seconds}s."
            )
            await self._refresh_coingecko_cache()

    async def _auto_refresh_loop(self) -> None:
        """
        Background Circuit Breaker recovery loop.
        ينام retry_delay_seconds ثم يُحاول Binance إذا كان محجوباً.
        يُشغَّل من main.py بعد startup.
        """
        while True:
            await asyncio.sleep(self.retry_delay_seconds)

            if not self.binance_blocked:
                continue

            log.info("Auto-refresh: retry window reached. Attempting Binance.")
            try:
                await self._refresh_binance_cache()

                if not self.binance_blocked:
                    # Circuit Breaker → CLOSED: Binance عاد
                    log.info("✅ Binance successfully restored via auto-refresh.")
                else:
                    log.warning(
                        "Binance still blocked after auto-refresh. "
                        f"Next retry in {self.retry_delay_seconds}s."
                    )
            except Exception as e:
                log.error(f"Auto-refresh retry failed: {e}")

    def is_valid_symbol(self, symbol: str, market: str) -> bool:
        """
        Validates a symbol against the populated cache.
        يعتمد على binance_blocked لتحديد منطق التحقق:
          - binance_blocked=True  → CoinGecko cache → True لكل الرموز
          - binance_blocked=False → Binance cache   → يتحقق من market
        """
        if not self._cache_populated:
            log.warning(
                "Symbol cache is not populated. "
                "Validation may be unreliable, allowing symbol through."
            )
            return True

        symbol_upper = (symbol or "").strip().upper()

        if symbol_upper not in self._symbols_cache:
            return False

        # إذا كان الكاش من CoinGecko (أثناء الحجب) → True دائماً
        if self.binance_blocked:
            return True

        available_markets = self._symbols_cache[symbol_upper]["markets"]
        market_lower = (market or "").lower()
        for available_market in available_markets:
            if market_lower in available_market.lower():
                return True

        return False

# --- END OF FINAL, HARDENED, AND PRODUCTION-READY FILE (Version 1.4.0) ---
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/market_data_service.py ---
