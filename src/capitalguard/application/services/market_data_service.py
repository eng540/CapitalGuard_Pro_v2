#--- START OF FINAL, HARDENED, AND PRODUCTION-READY FILE (Version 1.2.1) ---
# src/capitalguard/application/services/market_data_service.py
import logging
import asyncio
import os
from typing import Dict, Any, Set

import httpx
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient

log = logging.getLogger(__name__)

BINANCE_ENDPOINTS = {
    "Spot": "https://api.binance.com/api/v3/exchangeInfo",
    "Futures-USD-M": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "Futures-COIN-M": "https://dapi.binance.com/dapi/v1/exchangeInfo",
}

class MarketDataService:
    """
    A smart data provider service that can switch between sources.
    It attempts to use Binance by default but gracefully falls back to CoinGecko
    if it detects a geo-block (HTTP 451).
    """
    def __init__(self):
        self._symbols_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_populated = False
        self.provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()
        self.binance_blocked = False

    async def _fetch_from_binance_endpoint(self, client: httpx.AsyncClient, market: str, url: str) -> tuple[str, list]:
        """Fetches symbols from a single Binance endpoint, detecting geo-blocks."""
        try:
            response = await client.get(url, timeout=15.0)
            if response.status_code == 451:
                log.error(f"Binance GEO-BLOCK detected for {market} (HTTP 451). This is a location-based restriction from Binance.")
                self.binance_blocked = True
                return market, []
            response.raise_for_status()
            data = response.json()
            return market, data.get("symbols", [])
        except httpx.HTTPStatusError as e:
            log.error(f"Failed to fetch symbols for {market}: {e.response.status_code}")
        except Exception as e:
            log.error(f"An unexpected error occurred while fetching symbols for {market}: {e}")
        return market, []

    async def _refresh_binance_cache(self):
        """Fetches and consolidates symbols from all Binance endpoints."""
        log.info("Attempting to refresh symbols cache from Binance...")
        unified_cache: Dict[str, Dict[str, Any]] = {}
        
        async with httpx.AsyncClient() as client:
            tasks = [self._fetch_from_binance_endpoint(client, market, url) for market, url in BINANCE_ENDPOINTS.items()]
            results = await asyncio.gather(*tasks)

        successful_fetches = 0
        for market, symbols_list in results:
            if not symbols_list: continue
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
            log.info(f"Successfully populated symbols cache with {len(self._symbols_cache)} unique symbols from {successful_fetches} Binance endpoints.")
            if successful_fetches > 0:
                self.binance_blocked = False
        else:
            log.error("Failed to populate symbols cache from Binance. No symbols were fetched from any endpoint.")
            self._cache_populated = False
            self.binance_blocked = True

    async def _refresh_coingecko_cache(self):
        """Fetches and constructs a symbol list from CoinGecko."""
        log.info("Refreshing symbols cache from CoinGecko...")
        cg_client = CoinGeckoClient()
        symbols = await cg_client.get_all_symbols()
        self._symbols_cache = {s: {"markets": {"Spot", "Futures-USD-M"}} for s in symbols}
        self._cache_populated = bool(self._symbols_cache)
        if self._cache_populated:
            log.info(f"Successfully populated cache with {len(self._symbols_cache)} symbols from CoinGecko.")
        else:
            log.error("Failed to populate symbols cache from CoinGecko.")

    async def refresh_symbols_cache(self) -> None:
        """
        Main entry point for refreshing the cache. Implements the fallback logic.
        """
        if self.provider == "binance":
            await self._refresh_binance_cache()
            if self.binance_blocked:
                log.warning("All Binance endpoints failed or are geo-blocked. Falling back to CoinGecko for symbol data.")
                self.provider = "coingecko"
                # ✅ THE FIX: Update environment variable so other services know
                os.environ["MARKET_DATA_PROVIDER"] = "coingecko"
                os.environ["ENABLE_WATCHER"] = "0"
                await self._refresh_coingecko_cache()
        else:
            await self._refresh_coingecko_cache()

    def is_valid_symbol(self, symbol: str, market: str) -> bool:
        """
        Validates a symbol against the populated cache.
        """
        if not self._cache_populated:
            log.warning("Symbol cache is not populated. Validation may be unreliable, allowing symbol through.")
            return True

        symbol_upper = (symbol or "").strip().upper()
        
        if symbol_upper not in self._symbols_cache:
            return False

        if self.provider == "coingecko":
            return True

        available_markets = self._symbols_cache[symbol_upper]["markets"]
        market_lower = (market or "").lower()
        for available_market in available_markets:
            if market_lower in available_market.lower():
                return True

        return False

# --- END OF FINAL, HARDENED, AND PRODUCTION-READY FILE (Version 1.2.1) ---