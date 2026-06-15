#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/pricing/coingecko_client.py ---
# File: src/capitalguard/infrastructure/pricing/coingecko_client.py
# Version: v1.6.0-GLOBAL-CLIENT
#
# ✅ THE FIX (P2 — Global HTTP Client):
#   كان httpx.AsyncClient() يُنشأ ويُغلق في كل طلب — overhead في كل مرة.
#   الإصلاح: client واحد مشترك على مستوى الكلاس.
#
# ✅ محفوظ من v1.5.0:
#   Rate Limiting (6s بين الطلبات)
#   In-memory price cache (TTL 60s)
#   Adaptive backoff عند 429
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-17

import logging
import asyncio
import time
import httpx
from typing import Dict, Optional, Set, ClassVar

log = logging.getLogger(__name__)


class CoinGeckoClient:
    """
    Client CoinGecko مع Rate Limiting وGlobal HTTP Client.
    يُستخدم كـ fallback عند حجب Binance.
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    # ── Rate Limiting (class-level — مشترك) ──────────────────────────────
    _last_request_time: ClassVar[float] = 0.0
    _request_interval:  ClassVar[float] = 6.0   # ثانية واحدة كل 6 = ~10 req/min
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    # ── Price Cache (class-level — مشترك) ────────────────────────────────
    _price_cache: ClassVar[Dict[str, tuple]] = {}
    _cache_ttl: ClassVar[int] = 60

    # ✅ P2-FIX: Global HTTP Client ────────────────────────────────────────
    _client: ClassVar[Optional[httpx.AsyncClient]] = None

    @classmethod
    def _get_client(cls) -> httpx.AsyncClient:
        """يُعيد الـ client المشترك — يُنشئه إذا لم يكن موجوداً أو أُغلق."""
        if cls._client is None or cls._client.is_closed:
            cls._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0),
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            )
        return cls._client

    async def _wait_for_rate_limit(self) -> None:
        """يُطبِّق الانتظار بين الطلبات."""
        async with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._request_interval:
                wait = self._request_interval - elapsed
                log.debug("CoinGecko rate limit: waiting %.2fs", wait)
                await asyncio.sleep(wait)
            CoinGeckoClient._last_request_time = time.time()

    async def get_all_symbols(self) -> Set[str]:
        """جلب قائمة الرموز من CoinGecko."""
        try:
            await self._wait_for_rate_limit()
            client = self._get_client()
            response = await client.get(
                f"{self.BASE_URL}/coins/list", timeout=30.0
            )
            response.raise_for_status()
            coins = response.json()

            all_symbols = set()
            for coin in coins:
                if "symbol" in coin:
                    all_symbols.add(str(coin["symbol"]).upper())

            usdt_pairs = {f"{s}USDT" for s in all_symbols}
            log.info("Fetched %d base symbols from CoinGecko.", len(all_symbols))
            return usdt_pairs

        except httpx.RequestError as e:
            CoinGeckoClient._client = None
            log.error("CoinGecko get_all_symbols request error: %s", e)
            return set()
        except Exception as e:
            log.error("Failed to fetch symbols from CoinGecko: %s", e)
            return set()

    async def get_price(self, symbol: str) -> Optional[float]:
        """جلب سعر رمز واحد مع Cache وRate Limiting."""
        symbol = symbol.upper()

        # 1. فحص الـ Cache
        cached = self._price_cache.get(symbol)
        if cached:
            price, ts = cached
            if time.time() - ts < self._cache_ttl:
                return price

        if not symbol.endswith("USDT"):
            return None

        coin_id = symbol.replace("USDT", "").lower()

        # Mapping للرموز الشائعة
        id_map = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
            "XRP": "ripple",  "BNB": "binancecoin", "DOGE": "dogecoin",
        }
        coin_id = id_map.get(coin_id.upper(), coin_id)

        try:
            await self._wait_for_rate_limit()
            client = self._get_client()

            url    = f"{self.BASE_URL}/simple/price"
            params = {"ids": coin_id, "vs_currencies": "usd"}
            response = await client.get(url, params=params, timeout=10.0)

            if response.status_code == 429:
                log.warning("CoinGecko 429 for %s — backing off.", coin_id)
                CoinGeckoClient._request_interval += 2.0  # adaptive slowdown
                return None

            response.raise_for_status()
            data = response.json()

            if coin_id in data and "usd" in data[coin_id]:
                price = float(data[coin_id]["usd"])
                CoinGeckoClient._price_cache[symbol] = (price, time.time())
                return price

            log.warning("Price for '%s' not found in CoinGecko.", coin_id)
            return None

        except httpx.RequestError as e:
            CoinGeckoClient._client = None
            log.error("CoinGecko request error for %s: %s", coin_id, e)
            return None
        except httpx.HTTPStatusError as e:
            log.error("CoinGecko HTTP error for %s: %s", coin_id, e.response.status_code)
            return None
        except Exception as e:
            log.error("CoinGecko fetch failed for %s: %s", coin_id, e)
            return None
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/pricing/coingecko_client.py ---
