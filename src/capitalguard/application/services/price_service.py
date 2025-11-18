# File: src/capitalguard/application/services/price_service.py
# Version: v16.3.3-R2 (Combined Solution)
# ✅ THE FIX: Combined developer's filtering with our validation system

import logging
import os
import asyncio
from dataclasses import dataclass
from typing import Optional

from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.pricing.coingecko_client import CoinGeckoClient
from capitalguard.infrastructure.cache import InMemoryCache

log = logging.getLogger(__name__)
price_cache = InMemoryCache(ttl_seconds=60)

# ✅ From Developer: Known problematic symbols
_UNSUPPORTED_SYMBOLS = {"SUPRAUSDT", "FETCHAITETHERUS", "ENS/USDT"}

# ✅ From Our Solution: Valid trading symbols
_VALID_SYMBOLS = {
    'Futures': ['BTCUSDT', 'ETHUSDT', 'ADAUSDT', 'DOTUSDT', 'LINKUSDT', 
               'LTCUSDT', 'BCHUSDT', 'XRPUSDT', 'EOSUSDT', 'TRXUSDT',
               'ETCUSDT', 'XLMUSDT', 'ATOMUSDT', 'XTZUSDT', 'NEOUSDT',
               'IOTAUSDT', 'ONTUSDT', 'VETUSDT', 'MATICUSDT', 'ENSUSDT',
               'SOLUSDT', 'DOGEUSDT', 'AVAXUSDT', 'FILUSDT', 'ALGOUSDT',
               'FETUSDT'],  # ✅ Added corrected symbol
    'Spot': ['BTCUSDT', 'ETHUSDT', 'ADAUSDT', 'DOTUSDT', 'LINKUSDT']
}

@dataclass
class PriceService:
    
    def _normalize_symbol(self, symbol: str) -> str:
        """Normalizes symbol with automatic fixes"""
        symbol_upper = (symbol or "").strip().upper()
        
        # ✅ From Developer: Automatic fixes for known broken names
        if symbol_upper == "FETCHAITETHERUS":
            return "FETUSDT"
        if "ENS/USDT" in symbol_upper:
            return "ENSUSDT"
            
        # Existing normalization logic
        if any(pair in symbol_upper for pair in ["USDT", "PERP", "BTC", "ETH", "BUSD", "USDC"]):
            return symbol_upper.replace('/', '').replace('-', '')

        if 2 <= len(symbol_upper) <= 5 and symbol_upper.isalpha():
            normalized = f"{symbol_upper}USDT"
            log.debug("Normalizing symbol '%s' to '%s'", symbol, normalized)
            return normalized

        return symbol_upper.replace('/', '').replace('-', '')

    async def _is_valid_symbol(self, symbol: str, market: str) -> bool:
        """✅ Combined validation: unsupported + valid symbols check"""
        # Check against known problematic symbols
        if symbol in _UNSUPPORTED_SYMBOLS:
            log.warning(f"Symbol {symbol} is in unsupported list")
            return False
            
        # Check against valid symbols
        return symbol in _VALID_SYMBOLS.get(market, [])

    async def safe_get_cached_price(self, asset: str, market: str = "Futures", force_refresh: bool = False) -> Optional[float]:
        """
        ✅ From Our Solution: Safe price fetching with validation
        """
        try:
            # Normalize and validate
            clean_asset = self._normalize_symbol(asset)
            
            if not await self._is_valid_symbol(clean_asset, market):
                log.warning(f"Invalid trading symbol: {clean_asset} for market: {market}")
                return None
            
            # Proceed with normal price fetch
            return await self.get_cached_price(clean_asset, market, force_refresh)
            
        except Exception as e:
            log.error(f"Error in safe_get_cached_price for {asset}: {e}")
            return None

    async def get_cached_price(self, symbol: str, market: str, force_refresh: bool = False) -> Optional[float]:
        """
        ✅ Original function with developer's filtering enhancements
        """
        if not symbol:
            return None

        normalized_symbol = self._normalize_symbol(symbol)
        
        # ✅ From Developer: Final check against unsupported symbols
        if normalized_symbol in _UNSUPPORTED_SYMBOLS:
            log.warning(f"Attempted to fetch price for KNOWN UNSUPPORTED symbol: {symbol}")
            return None

        # ... [rest of existing get_cached_price logic] ...
        # Existing provider selection and caching logic remains the same