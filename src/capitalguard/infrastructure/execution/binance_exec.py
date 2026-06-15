# --- START OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.4) ---
# src/capitalguard/infrastructure/execution/binance_exec.py

import time
import hmac
import hashlib
import logging
from urllib.parse import urlencode
from dataclasses import dataclass
from typing import Optional, Dict, Any

import httpx

log = logging.getLogger(__name__)

BINANCE_SPOT_BASE = "https://api.binance.com"
BINANCE_FUTU_BASE = "https://fapi.binance.com"

@dataclass
class BinanceCreds:
    api_key: str
    api_secret: str

@dataclass
class OrderResult:
    ok: bool
    payload: Dict[str, Any]
    message: str = ""

class BinanceExec:
    """
    An asynchronous client for executing signed requests to Binance (Spot/Futures).
    This version uses httpx.AsyncClient for non-blocking network operations, making it
    safe to use in async contexts like FastAPI and PTB handlers.
    """
    def __init__(self, creds: BinanceCreds, futures: bool = False, recv_window: int = 5000, timeout: float = 10.0):
        self.creds = creds
        self.base_url = BINANCE_FUTU_BASE if futures else BINANCE_SPOT_BASE
        self.is_futures = futures
        self.recv_window = recv_window
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-MBX-APIKEY": self.creds.api_key},
            timeout=self.timeout
        )

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Creates the required HMAC-SHA256 signature for signed endpoints."""
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.recv_window
        query_string = urlencode(params, doseq=True)
        signature = hmac.new(self.creds.api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        return params

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None, signed: bool = False) -> httpx.Response:
        """Performs an async GET request."""
        request_params = params.copy() if params else {}
        if signed:
            request_params = self._sign(request_params)
        return await self.client.get(path, params=request_params)

    async def _post(self, path: str, params: Optional[Dict[str, Any]] = None, signed: bool = False) -> httpx.Response:
        """Performs an async POST request."""
        request_params = params.copy() if params else {}
        if signed:
            request_params = self._sign(request_params)
        return await self.client.post(path, params=request_params)

    async def exchange_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetches exchange information for a specific symbol."""
        try:
            path = "/fapi/v1/exchangeInfo" if self.is_futures else "/api/v3/exchangeInfo"
            response = await self._get(path, {"symbol": symbol.upper()})
            if response.status_code == 200:
                data = response.json()
                symbols = data.get("symbols") or []
                return symbols[0] if symbols else None
            log.warning("Failed to fetch exchangeInfo for %s: %s", symbol, response.text[:200])
        except Exception as e:
            log.exception("Error fetching exchange_info for %s: %s", symbol, e)
        return None

    async def account_balance(self) -> Optional[float]:
        """Fetches the account balance (USDT for Spot, availableBalance for Futures)."""
        try:
            if self.is_futures:
                response = await self._get("/fapi/v2/balance", signed=True)
                if response.status_code == 200:
                    for item in response.json():
                        if item.get("asset") == "USDT":
                            return float(item.get("availableBalance", 0))
            else:
                response = await self._get("/api/v3/account", signed=True)
                if response.status_code == 200:
                    for item in response.json().get("balances", []):
                        if item.get("asset") == "USDT":
                            return float(item.get("free", 0))
            log.warning("Failed to fetch account balance: %s", response.text[:200])
        except Exception as e:
            log.exception("Error fetching account_balance: %s", e)
        return None

    async def place_order(
        self, *, symbol: str, side: str, order_type: str, quantity: float,
        price: Optional[float] = None, time_in_force: str = "GTC", reduce_only: bool = False
    ) -> OrderResult:
        """Places a new order on the exchange."""
        try:
            params = {
                "symbol": symbol.upper(),
                "side": side.upper(),
                "type": order_type.upper(),
                "quantity": f"{quantity:.10f}".rstrip("0").rstrip("."),
            }
            if price is not None and order_type.upper() == "LIMIT":
                params["price"] = f"{price:.10f}".rstrip("0").rstrip(".")
                params["timeInForce"] = time_in_force

            path = "/fapi/v1/order" if self.is_futures else "/api/v3/order"
            if self.is_futures and reduce_only:
                params["reduceOnly"] = "true"

            response = await self._post(path, params, signed=True)
            if response.status_code == 200:
                return OrderResult(ok=True, payload=response.json())
            return OrderResult(ok=False, payload={}, message=response.text)
        except Exception as e:
            log.exception("Failed to place order for %s: %s", symbol, e)
            return OrderResult(ok=False, payload={}, message=str(e))

# --- END OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.4) ---