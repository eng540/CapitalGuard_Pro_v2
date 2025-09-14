#START src/capitalguard/infrastructure/pricing/coingecko_client.py
import logging
import httpx
from typing import List, Dict, Optional, Set

log = logging.getLogger(__name__)

class CoinGeckoClient:
    BASE_URL = "https://api.coingecko.com/api/v3"

    async def get_all_symbols(self) -> Set[str]:
        """Fetches all possible symbols from CoinGecko."""
        all_symbols = set()
        try:
            async with httpx.AsyncClient() as client:
                # Fetch coins list
                coins_list_url = f"{self.BASE_URL}/coins/list"
                response = await client.get(coins_list_url, timeout=30.0)
                response.raise_for_status()
                coins = response.json()
                # We only care about the 'symbol' which is usually what's traded (e.g., 'btc')
                for coin in coins:
                    all_symbols.add(str(coin['symbol']).upper())

                # CoinGecko doesn't have a direct USDT pair list, so we assume common pairs.
                # This is a limitation, but good enough for a fallback.
                # We will construct common USDT pairs.
                usdt_pairs = {f"{symbol}USDT" for symbol in all_symbols}
                log.info(f"Fetched {len(all_symbols)} base symbols from CoinGecko, constructed USDT pairs.")
                return usdt_pairs
        except Exception as e:
            log.error(f"Failed to fetch symbols from CoinGecko: {e}")
            return set()

    async def get_prices(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """
        Fetches prices for a list of symbols. Note: CoinGecko uses IDs, not symbols.
        This is a simplified version for fallback and might not work for all symbols.
        A more robust implementation would map symbols to CoinGecko IDs.
        """
        # This is a placeholder for a more complex price fetching logic if needed.
        # For now, we will rely on the PriceService to call this for single symbols.
        log.warning("Bulk price fetching from CoinGecko is not implemented. Use single price fetches.")
        return {s: None for s in symbols}

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Simplified single price fetch. Assumes symbol is the coin ID.
        Example: symbol 'BTCUSDT' -> we need to extract 'bitcoin'.
        This is non-trivial. A better approach is needed for production.
        For this hotfix, we will assume the price service handles this.
        """
        # This logic will be handled by the PriceService which will adapt the symbol.
        return None
#END