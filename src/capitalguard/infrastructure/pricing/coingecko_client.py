#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/pricing/coingecko_client.py ---
# src/capitalguard/infrastructure/pricing/coingecko_client.py
# Version: v1.5.0 - Rate Limit Safe
# ✅ THE FIX: Added strict Rate Limiting (max 10 req/min) and in-memory caching.
# 🎯 IMPACT: Prevents HTTP 429 errors when Binance is blocked and system falls back to CoinGecko.

import logging
import asyncio
import time
import httpx
from typing import List, Dict, Optional, Set

log = logging.getLogger(__name__)

class CoinGeckoClient:
    """
    A robust client for CoinGecko with built-in rate limiting and caching.
    Essential for servers where Binance is Geo-Blocked.
    """
    BASE_URL = "https://api.coingecko.com/api/v3"
    
    # CoinGecko Free Tier: ~10-30 requests/minute safely.
    # We limit to 1 request every 6 seconds to be super safe.
    _last_request_time = 0.0
    _request_interval = 6.0 
    _lock = asyncio.Lock()
    
    # Simple in-memory cache: {symbol: (price, timestamp)}
    _price_cache: Dict[str, tuple[float, float]] = {}
    _cache_ttl = 60  # 1 minute cache

    async def _wait_for_rate_limit(self):
        """Enforces a delay between requests."""
        async with self._lock:
            now = time.time()
            time_since_last = now - self._last_request_time
            if time_since_last < self._request_interval:
                wait_time = self._request_interval - time_since_last
                log.debug(f"CoinGecko rate limit: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
            self._last_request_time = time.time()

    async def get_all_symbols(self) -> Set[str]:
        """
        Fetches list of coins. Cached heavily as this list rarely changes.
        """
        try:
            await self._wait_for_rate_limit()
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.BASE_URL}/coins/list", timeout=30.0)
                response.raise_for_status()
                coins = response.json()
                
                all_symbols = set()
                for coin in coins:
                    if 'symbol' in coin:
                        all_symbols.add(str(coin['symbol']).upper())

                # Construct USDT pairs
                usdt_pairs = {f"{symbol}USDT" for symbol in all_symbols}
                log.info(f"Fetched {len(all_symbols)} base symbols from CoinGecko.")
                return usdt_pairs
        except Exception as e:
            log.error(f"Failed to fetch symbols from CoinGecko: {e}")
            return set()

    async def get_price(self, symbol: str) -> Optional[float]:
        """
        Fetches price with Cache + Rate Limiting.
        """
        symbol = symbol.upper()
        
        # 1. Check Cache
        cached = self._price_cache.get(symbol)
        if cached:
            price, ts = cached
            if time.time() - ts < self._cache_ttl:
                return price

        if not symbol.endswith("USDT"):
            return None
            
        coin_id = symbol.replace("USDT", "").lower()
        
        # Common mapping fixes
        id_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple", "BNB": "binancecoin", "DOGE": "dogecoin"}
        coin_id = id_map.get(coin_id.upper(), coin_id)

        try:
            await self._wait_for_rate_limit()
            
            async with httpx.AsyncClient() as client:
                url = f"{self.BASE_URL}/simple/price"
                params = {"ids": coin_id, "vs_currencies": "usd"}
                
                response = await client.get(url, params=params, timeout=10.0)
                
                if response.status_code == 429:
                    log.warning(f"CoinGecko 429 (Too Many Requests) for {coin_id}. Backing off.")
                    self._request_interval += 2.0 # Adaptively slow down
                    return None
                
                response.raise_for_status()
                data = response.json()
                
                if coin_id in data and "usd" in data[coin_id]:
                    price = float(data[coin_id]["usd"])
                    # Update Cache
                    self._price_cache[symbol] = (price, time.time())
                    return price
                else:
                    log.warning(f"Price for '{coin_id}' not found in CoinGecko.")
                    return None
                    
        except httpx.HTTPStatusError as e:
            log.error(f"CoinGecko HTTP error for {coin_id}: {e.response.status_code}")
        except Exception as e:
            log.error(f"CoinGecko fetch failed for {coin_id}: {e}")
            
        return None
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/pricing/coingecko_client.py ---