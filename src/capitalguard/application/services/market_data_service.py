# --- START OF NEW FILE: src/capitalguard/application/services/market_data_service.py ---
import logging
import asyncio
from typing import Dict, Any, Set

import httpx

log = logging.getLogger(__name__)

# نقاط النهاية لكل سوق من أسواق Binance
BINANCE_ENDPOINTS = {
    "Spot": "https://api.binance.com/api/v3/exchangeInfo",
    "Futures-USD-M": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "Futures-COIN-M": "https://dapi.binance.com/dapi/v1/exchangeInfo",
}

class MarketDataService:
    """
    خدمة مركزية مسؤولة عن جلب وتخزين بيانات الأصول من جميع أسواق Binance.
    توفر ذاكرة تخزين مؤقت (cache) موحدة لجميع الأصول القابلة للتداول.
    """
    def __init__(self):
        self._symbols_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_populated = False

    async def _fetch_from_endpoint(self, client: httpx.AsyncClient, market: str, url: str) -> tuple[str, list]:
        """يجلب الأصول من نقطة نهاية واحدة."""
        try:
            response = await client.get(url, timeout=15.0)
            response.raise_for_status()
            data = response.json()
            return market, data.get("symbols", [])
        except httpx.HTTPStatusError as e:
            log.error(f"Failed to fetch symbols for {market}: {e.response.status_code} - {e.response.text[:100]}")
        except Exception as e:
            log.error(f"An unexpected error occurred while fetching symbols for {market}: {e}")
        return market, []

    async def refresh_symbols_cache(self) -> None:
        """
        يجلب البيانات من جميع نقاط النهاية بشكل متوازٍ ويوحدها في cache واحد.
        """
        log.info("Starting to refresh Binance symbols cache for all markets...")
        unified_cache: Dict[str, Dict[str, Any]] = {}
        
        async with httpx.AsyncClient() as client:
            tasks = [self._fetch_from_endpoint(client, market, url) for market, url in BINANCE_ENDPOINTS.items()]
            results = await asyncio.gather(*tasks)

        for market, symbols_list in results:
            if not symbols_list:
                continue
            
            for symbol_data in symbols_list:
                if symbol_data.get("status") == "TRADING":
                    symbol_name = symbol_data["symbol"].upper()
                    
                    # تهيئة القاموس للرمز إذا لم يكن موجودًا
                    if symbol_name not in unified_cache:
                        unified_cache[symbol_name] = {"markets": set()}
                    
                    # إضافة السوق الحالي إلى قائمة الأسواق المدعومة لهذا الرمز
                    unified_cache[symbol_name]["markets"].add(market)

        if not unified_cache:
            log.error("Failed to populate symbols cache. No symbols were fetched from any endpoint.")
            self._cache_populated = False
            return

        self._symbols_cache = unified_cache
        self._cache_populated = True
        log.info(f"Successfully populated symbols cache with {len(self._symbols_cache)} unique symbols from all markets.")

    def is_valid_symbol(self, symbol: str, market: str) -> bool:
        """
        يتحقق مما إذا كان الرمز صالحًا ومتوفرًا في السوق المحدد.
        """
        if not self._cache_populated:
            log.warning("Symbol cache is not populated. Validation may be unreliable.")
            # في هذه الحالة، قد نسمح بالمرور لتجنب تعطيل النظام، لكن مع تحذير.
            return True

        symbol_upper = symbol.strip().upper()
        
        if symbol_upper not in self._symbols_cache:
            return False # الرمز غير موجود في أي سوق

        # التحقق من توافق السوق
        # "Futures" يعتبر متوافقًا مع "Futures-USD-M" و "Futures-COIN-M"
        available_markets = self._symbols_cache[symbol_upper]["markets"]
        market_lower = market.lower()

        for available_market in available_markets:
            if market_lower in available_market.lower():
                return True # وجدنا تطابقًا

        return False
# --- END OF NEW FILE ---