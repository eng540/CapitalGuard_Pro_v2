#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/price_service.py ---
# File: src/capitalguard/application/services/price_service.py
# Version: v17.0.0-WS-FIRST
#
# ✅ THE FIX — توحيد مصدر الأسعار (WebSocket أولاً):
#
# المشكلة التي كانت في v16:
#   price_service كان يقرأ من InMemoryCache منفصل بمفاتيح مختلفة تماماً:
#     "price:any:futures:BTCUSDT"  ← دائماً فارغ
#   فيقفز مباشرة لـ Binance REST → 429 Too Many Requests
#   بينما PriceStreamer (WebSocket) يكتب في core_cache:
#     "price:FUTURES:BTCUSDT" → 71000.0  ← لم يقرأه أحد!
#
# Pipeline الجديد — 4 مراحل بالترتيب:
#
#   L0: InMemoryCache (price_cache)
#       sync، بدون event loop، أسرع ما يكون
#       يُخزِّن نتائج L1/L2/L3 لـ 60 ثانية لتجنب تكرار البحث
#
#   L1: core_cache (WebSocket)
#       يقرأ مفاتيح "price:FUTURES:{symbol}" / "price:SPOT:{symbol}"
#       نفس المفاتيح التي يكتبها PriceStreamer → صفر REST calls
#       في حالات الاستخدام الاعتيادية
#
#   L2: Binance REST (async - httpx)
#       فقط إذا WS لم يُغذِّ الكاش بعد (رمز جديد أو startup)
#       أو عند force_refresh=True (أوامر MARKET)
#
#   L3: CoinGecko
#       fallback نهائي عند حجب Binance REST (429/451)
#
# ✅ محفوظ من الأصل:
#   - price_cache (InMemoryCache) كـ module-level instance
#   - import asyncio
#   - get_preview_price() alias
#   - _normalize_symbol()
#   - force_refresh parameter
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

from capitalguard.infrastructure.cache import InMemoryCache
from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient

log = logging.getLogger(__name__)

# ✅ محفوظ: L0 cache — sync، بلا event loop، thread-safe
# يُقلِّل الضغط على core_cache وREST calls
price_cache = InMemoryCache(ttl_seconds=60)


@dataclass
class PriceService:
    """
    خدمة الأسعار الموحَّدة — Pipeline رباعي المراحل.

    L0: InMemoryCache  → sync، أسرع مصدر
    L1: WebSocket/core_cache → يقرأ ما كتبه PriceStreamer
    L2: Binance REST   → fallback عند غياب WS
    L3: CoinGecko      → fallback نهائي
    """

    # ─────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────

    def _normalize_symbol(self, symbol: str) -> str:
        """يُطبِّع الرمز إلى صيغة زوج التداول الكاملة (BTC → BTCUSDT)."""
        symbol_upper = (symbol or "").strip().upper()
        if any(pair in symbol_upper for pair in
               ("USDT", "PERP", "BTC", "ETH", "BUSD", "USDC")):
            return symbol_upper
        if 2 <= len(symbol_upper) <= 5 and symbol_upper.isalpha():
            normalized = f"{symbol_upper}USDT"
            log.debug("Normalizing symbol '%s' to '%s'", symbol, normalized)
            return normalized
        return symbol_upper

    def _l0_key(self, symbol: str, market: str) -> str:
        """مفتاح L0 (InMemoryCache) — محلي لهذه الخدمة."""
        return f"price:any:{(market or 'spot').lower()}:{symbol}"

    async def _get_from_ws_cache(
        self, symbol: str, market: str
    ) -> Optional[float]:
        """
        L1: يقرأ من core_cache بنفس مفاتيح PriceStreamer:
          "price:FUTURES:{symbol}"  أو  "price:SPOT:{symbol}"
        """
        try:
            from capitalguard.infrastructure.core_engine import core_cache

            market_upper = (market or "Futures").upper()
            if "SPOT" in market_upper:
                primary_key   = f"price:SPOT:{symbol}"
                secondary_key = f"price:FUTURES:{symbol}"
            else:
                primary_key   = f"price:FUTURES:{symbol}"
                secondary_key = f"price:SPOT:{symbol}"

            price = await core_cache.get(primary_key)
            if price is not None:
                return float(price)

            price = await core_cache.get(secondary_key)
            if price is not None:
                return float(price)

        except Exception as e:
            log.debug("WS cache lookup failed for %s: %s", symbol, e)
        return None

    async def _write_back_cache(
        self, symbol: str, market: str, price: float
    ) -> None:
        """
        يُخزِّن السعر في L0 وL1 معاً لتوحيد المصدر.
        يستفيد منه أي استدعاء لاحق قبل وصول تيك WS جديد.
        """
        # L0 — sync، لا يفشل
        try:
            cache_key = self._l0_key(symbol, market)
            price_cache.set(cache_key, price, ttl_seconds=60)
        except Exception:
            pass

        # L1 — async، قد يفشل في بعض الـ loop contexts
        try:
            from capitalguard.infrastructure.core_engine import core_cache
            market_upper = (market or "Futures").upper()
            ws_key = (
                f"price:SPOT:{symbol}"
                if "SPOT" in market_upper
                else f"price:FUTURES:{symbol}"
            )
            await core_cache.set(ws_key, price, ttl=60)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    async def get_cached_price(
        self,
        symbol: str,
        market: str,
        force_refresh: bool = False,
    ) -> Optional[float]:
        """
        يُعيد سعر الرمز عبر pipeline رباعي المراحل.

        force_refresh=True:
          يتخطى L0 وL1 ويجلب مباشرة من L2/L3.
          يُستخدم لأوامر MARKET التي تحتاج السعر اللحظي الدقيق.
        """
        if not symbol:
            return None

        normalized = self._normalize_symbol(symbol)

        # ── L0: InMemoryCache (sync — بلا event loop) ─────────────
        if not force_refresh:
            try:
                cached = price_cache.get(self._l0_key(normalized, market))
                if cached is not None:
                    return cached
            except Exception:
                pass

        # ── L1: WebSocket core_cache ───────────────────────────────
        if not force_refresh:
            ws_price = await self._get_from_ws_cache(normalized, market)
            if ws_price is not None:
                log.debug(
                    "Price for %s from WebSocket cache: %s",
                    normalized, ws_price,
                )
                # كتابة في L0 للمرات القادمة
                try:
                    price_cache.set(
                        self._l0_key(normalized, market),
                        ws_price,
                        ttl_seconds=60,
                    )
                except Exception:
                    pass
                return ws_price

        # ── L2: Binance REST ────────────────────────────────────────
        provider_env = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        live_price: Optional[float] = None

        if provider_env == "binance":
            try:
                is_spot = str(market or "Spot").lower().startswith("spot")
                # ✅ async مباشرة — BinancePricing.get_price أصبحت async
                live_price = await BinancePricing.get_price(normalized, is_spot)
            except Exception as e:
                log.warning("Binance REST failed for %s: %s", normalized, e)
                live_price = None

        # ── L3: CoinGecko (fallback نهائي) ─────────────────────────
        if live_price is None:
            if provider_env == "binance":
                log.info(
                    "Binance REST unavailable for %s — "
                    "falling back to CoinGecko.",
                    normalized,
                )
            try:
                cg_client = CoinGeckoClient()
                live_price = await cg_client.get_price(normalized)
                if live_price:
                    log.info(
                        "CoinGecko price for %s: %s", normalized, live_price
                    )
            except Exception as e:
                log.error("CoinGecko failed for %s: %s", normalized, e)
                live_price = None

        # ── Write-back: حفظ في L0 وL1 ─────────────────────────────
        if live_price is not None:
            await self._write_back_cache(normalized, market, live_price)
            return live_price

        log.error("All price providers failed for %s", normalized)
        return None

    # backward-compatible alias
    async def get_preview_price(
        self, symbol: str, market: str, force_refresh: bool = False
    ) -> Optional[float]:
        return await self.get_cached_price(symbol, market, force_refresh)
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/price_service.py ---
