# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/pricing/binance.py ---
"""
Production-ready price fetcher (v1.1.0 - Resiliency Hotfix)
✅ THE FIX: Added explicit check `price_val is not None` before float() conversion
    to prevent `TypeError` when Binance returns a valid JSON error message
    (like symbol not found) which results in `data.get("price")` returning None.
"""
from __future__ import annotations
import requests
import logging
from typing import Optional, Dict

log = logging.getLogger(__name__)

BINANCE_SPOT_TICKER = "https://api.binance.com/api/v3/ticker/price"
BINANCE_FUT_TICKER = "https://fapi.binance.com/fapi/v1/ticker/price"


class BinancePricing:
    """
    Provides methods for fetching asset prices from Binance.
    It supports fetching a single price or all prices in a batch for efficiency.
    """

    @staticmethod
    def get_price(symbol: str, spot: bool = True, timeout: float = 4.0) -> Optional[float]:
        """Fetches the price for a single symbol."""
        url = BINANCE_SPOT_TICKER if spot else BINANCE_FUT_TICKER
        try:
            r = requests.get(url, params={"symbol": symbol.upper()}, timeout=timeout)
            if not r.ok:
                log.warning("Binance single price fetch failed for %s: %s", symbol, r.text[:200])
                return None

            data = r.json()

            # ✅ THE FIX: Check if "price" key exists and is not None before conversion
            price_val = data.get("price")
            if price_val is not None:
                return float(price_val)
            else:
                # Log if price is missing (e.g., {"code":-1121,"msg":"Invalid symbol."})
                log.warning("Binance price fetch for %s returned OK but no 'price' key. Response: %s", symbol, data)
                return None

        except requests.RequestException as e:
            log.error("Binance request exception for single price fetch %s: %s", symbol, e)
            return None
        except Exception as e:
            # This will catch the float() conversion error if price_val was None (now fixed)
            log.warning("An unexpected error occurred during single price fetch for %s: %s", symbol, e)
            return None

    @staticmethod
    def get_all_prices(spot: bool = True, timeout: float = 8.0) -> Dict[str, float]:
        """
        ✅ NEW & HIGHLY EFFICIENT: Fetches prices for ALL available symbols in a single API call.
        This is the preferred method for batch operations like the AlertService.

        Returns:
            A dictionary mapping symbol names to their prices, e.g., {"BTCUSDT": 60000.0, ...}.
        """
        url = BINANCE_SPOT_TICKER if spot else BINANCE_FUT_TICKER
        price_map: Dict[str, float] = {}
        try:
            # The endpoint without a 'symbol' parameter returns all prices.
            r = requests.get(url, timeout=timeout)
            if not r.ok:
                log.error("Binance bulk price fetch failed: %s", r.text[:200])
                return price_map

            data = r.json()

            # The response is a list of dictionaries: [{"symbol": "BTCUSDT", "price": "60000.00"}, ...]
            for item in data:
                try:
                    symbol = item.get("symbol")
                    price = item.get("price")
                    if symbol and price is not None:  # ✅ Also check for None here
                        price_map[symbol] = float(price)
                except (ValueError, TypeError):
                    # Log and skip if a single item is malformed, but continue processing others.
                    log.warning("Could not parse price for item: %s", item)
                    continue

            return price_map

        except requests.RequestException as e:
            log.error("Binance request exception during bulk price fetch: %s", e)
            return price_map  # Return empty map on failure
        except Exception as e:
            log.error("An unexpected error occurred during bulk price fetch: %s", e, exc_info=True)
            return price_map
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/pricing/binance.py ---