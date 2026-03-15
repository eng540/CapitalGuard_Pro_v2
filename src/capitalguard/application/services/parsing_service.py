# src/capitalguard/application/services/price_service.py
"""
File: src/capitalguard/application/services/price_service.py
Version: v17.0.0-WS-FIRST

✅ THE FIX — توحيد مصدر الأسعار (WebSocket أولاً):

المشكلة:
  كانت هناك مصدران منفصلان تماماً لجلب الأسعار:
    1. WebSocket (PriceStreamer) يكتب في core_cache:
         "price:FUTURES:BTCUSDT" → 71000.0
    2. price_service يقرأ من InMemoryCache منفصل:
         "price:any:futures:BTCUSDT" → فارغ → يستدعي Binance REST → 429

  النتيجتان:
    - 429 Too Many Requests على IP Railway المشترك
    - تأخير في كل استدعاء (HTTP round-trip)

الإصلاح — Pipeline جديد بثلاث مراحل:
  المرحلة 1: core_cache (WebSocket) ← صفر HTTP calls، أسرع مصدر
  المرحلة 2: BinancePricing REST   ← فقط إذا WS لم يُغذِّ بعد
  المرحلة 3: CoinGecko             ← fallback نهائي

مفاتيح core_cache التي يكتبها PriceStreamer:
  "price:FUTURES:{SYMBOL}"
  "price:SPOT:{SYMBOL}"

مفاتيح core_cache التي تقرأها get_live_price() في ui_texts.py:
  "price:{MARKET.UPPER()}:{SYMBOL}"

✅ price_service الآن يقرأ بنفس المفاتيح → مصدر واحد موحَّد.

Reviewed-by: Guardian Protocol v1 — 2026-03-15
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient

log = logging.getLogger(__name__)


@dataclass
class PriceService:
    """
    خدمة الأسعار الموحَّدة.
    Pipeline: WebSocket cache → Binance REST → CoinGecko
    """

    # ─────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────

    def _normalize_symbol(self, symbol: str) -> str:
        """يُطبِّق صيغة موحَّدة للرمز (BTCUSDT لا BTC)."""
        symbol_upper = (symbol or "").strip().upper()
        if any(p in symbol_upper for p in
               ("USDT", "PERP", "BTC", "ETH", "BUSD", "USDC")):
            return symbol_upper
        if 2 <= len(symbol_upper) <= 5 and symbol_upper.isalpha():
            normalized = f"{symbol_upper}USDT"
            log.debug("Normalizing '%s' → '%s'", symbol, normalized)
            return normalized
        return symbol_upper

    async def _get_from_ws_cache(
        self, symbol: str, market: str
    ) -> Optional[float]:
        """
        ✅ المرحلة 1: يقرأ من core_cache الذي يُكتب من WebSocket.
        مفاتيح متوافقة مع PriceStreamer._handle_price() وget_live_price().
        """
        try:
            from capitalguard.infrastructure.core_engine import core_cache

            # المفتاح الأساسي (يتطابق مع ما يكتبه PriceStreamer)
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
        Pipeline موحَّد للحصول على السعر:

        1. core_cache (WebSocket) — صفر HTTP calls
        2. Binance REST           — فقط عند الحاجة
        3. CoinGecko              — fallback نهائي

        force_refresh=True يتخطى المرحلة 1 (للأوامر MARKET التي تحتاج
        السعر اللحظي الدقيق عند إنشاء التوصية).
        """
        if not symbol:
            return None

        normalized = self._normalize_symbol(symbol)

        # ──────────────────────────────────────────────────────────
        # المرحلة 1: WebSocket cache (core_cache)
        # ──────────────────────────────────────────────────────────
        if not force_refresh:
            ws_price = await self._get_from_ws_cache(normalized, market)
            if ws_price is not None:
                log.debug(
                    "Price for %s served from WebSocket cache: %s",
                    normalized, ws_price,
                )
                return ws_price

        # ──────────────────────────────────────────────────────────
        # المرحلة 2: Binance REST (async — لا blocking)
        # تُستخدم فقط إذا:
        #   a) WS لم يُشترك بهذا الرمز بعد (رمز جديد/startup)
        #   b) force_refresh=True (أوامر MARKET)
        # ──────────────────────────────────────────────────────────
        provider_env = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        live_price: Optional[float] = None

        if provider_env == "binance":
            try:
                is_spot = str(market or "Spot").lower().startswith("spot")
                # ✅ async مباشرة — لا run_in_executor (BinancePricing أصبحت async)
                live_price = await BinancePricing.get_price(normalized, is_spot)
            except Exception as e:
                log.warning("Binance REST failed for %s: %s", normalized, e)
                live_price = None

        # ──────────────────────────────────────────────────────────
        # المرحلة 3: CoinGecko (fallback نهائي)
        # ──────────────────────────────────────────────────────────
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
                log.error("CoinGecko fallback failed for %s: %s", normalized, e)
                live_price = None

        # ──────────────────────────────────────────────────────────
        # كتابة النتيجة في core_cache لتوحيد المصدر
        # (يفيد الاستدعاءات التالية قبل أن يصل تيك WS)
        # ──────────────────────────────────────────────────────────
        if live_price is not None:
            try:
                from capitalguard.infrastructure.core_engine import core_cache
                market_upper = (market or "Futures").upper()
                cache_key = (
                    f"price:SPOT:{normalized}"
                    if "SPOT" in market_upper
                    else f"price:FUTURES:{normalized}"
                )
                await core_cache.set(cache_key, live_price, ttl=60)
            except Exception:
                pass
            return live_price

        log.error("All price providers failed for %s", normalized)
        return None

    # backward-compatible alias
    async def get_preview_price(
        self, symbol: str, market: str, force_refresh: bool = False
    ) -> Optional[float]:
        return await self.get_cached_price(symbol, market, force_refresh)
