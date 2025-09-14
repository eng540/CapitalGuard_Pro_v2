#START src/capitalguard/infrastructure/pricing/coingecko_client.py
import logging
import httpx
from typing import List, Dict, Optional, Set

log = logging.getLogger(__name__)

class CoinGeckoClient:
    """
    A client for interacting with the CoinGecko API.
    Used as a fallback data source when Binance is unavailable.
    """
    BASE_URL = "https://api.coingecko.com/api/v3"

    async def get_all_symbols(self) -> Set[str]:
        """
        Fetches a list of all known coin symbols from CoinGecko and constructs
        common USDT trading pairs. This is an approximation as CoinGecko
        does not provide a direct list of trading pairs like Binance.
        """
        all_symbols = set()
        try:
            async with httpx.AsyncClient() as client:
                # Fetch the full list of coins
                coins_list_url = f"{self.BASE_URL}/coins/list"
                response = await client.get(coins_list_url, timeout=30.0)
                response.raise_for_status()
                coins = response.json()
                
                # Extract the 'symbol' for each coin (e.g., 'btc', 'eth')
                for coin in coins:
                    if 'symbol' in coin:
                        all_symbols.add(str(coin['symbol']).upper())

                # Construct common USDT pairs from the base symbols
                usdt_pairs = {f"{symbol}USDT" for symbol in all_symbols}
                log.info(f"Fetched {len(all_symbols)} base symbols from CoinGecko, constructed {len(usdt_pairs)} USDT pairs.")
                return usdt_pairs
        except httpx.HTTPStatusError as e:
            log.error(f"HTTP error while fetching symbols from CoinGecko: {e.response.status_code}")
        except Exception as e:
            log.error(f"Failed to fetch symbols from CoinGecko: {e}")
        
        return set()

    async def get_price(self, symbol: str) -> Optional[float]:
        """
        Fetches the current price of a single symbol (e.g., 'BTCUSDT') from CoinGecko.
        It does this by mapping the symbol to a CoinGecko coin ID.
        """
        # This is a simplified mapping. A production system might need a more robust
        # symbol-to-id mapping service.
        if not symbol.upper().endswith("USDT"):
            log.warning(f"CoinGecko price fetch only supports USDT pairs. Cannot fetch '{symbol}'.")
            return None
            
        coin_id = symbol.upper().replace("USDT", "").lower()
        
        # Special mappings for common discrepancies
        id_map = {
            # "SYMBOL": "coingecko_id"
        }
        coin_id = id_map.get(coin_id.upper(), coin_id)

        price_url = f"{self.BASE_URL}/simple/price"
        params = {
            "ids": coin_id,
            "vs_currencies": "usd"
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(price_url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                if coin_id in data and "usd" in data[coin_id]:
                    return float(data[coin_id]["usd"])
                else:
                    log.warning(f"Price for coin ID '{coin_id}' not found in CoinGecko response.")
                    return None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                log.warning(f"Coin ID '{coin_id}' not found on CoinGecko (404).")
            else:
                log.error(f"HTTP error fetching price for '{coin_id}' from CoinGecko: {e.response.status_code}")
        except Exception as e:
            log.error(f"Failed to fetch price for '{coin_id}' from CoinGecko: {e}")
            
        return None
#END