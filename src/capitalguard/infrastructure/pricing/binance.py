# --- START OF FILE: src/capitalguard/infrastructure/pricing/binance.py ---
from __future__ import annotations
import requests
import logging

log = logging.getLogger(__name__)

BINANCE_SPOT_TICKER = "https://api.binance.com/api/v3/ticker/price"
BINANCE_FUT_TICKER  = "https://fapi.binance.com/fapi/v1/ticker/price"

class BinancePricing:
    """جلب سعر حديث للعرض فقط (لا تنفيذ صفقات)."""

    @staticmethod
    def get_price(symbol: str, spot: bool = True, timeout: float = 4.0) -> float | None:
        url = BINANCE_SPOT_TICKER if spot else BINANCE_FUT_TICKER
        try:
            r = requests.get(url, params={"symbol": symbol.upper()}, timeout=timeout)
            if not r.ok:
                log.warning("Binance price failed %s: %s", symbol, r.text[:200])
                return None
            data = r.json()
            return float(data.get("price"))
        except Exception as e:
            log.warning("Binance exception: %s", e)
            return None
# --- END OF FILE ---