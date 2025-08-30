# --- START OF FILE: src/capitalguard/infrastructure/execution/binance_exec.py ---
from __future__ import annotations
import time, hmac, hashlib, requests, logging, os
from urllib.parse import urlencode
from dataclasses import dataclass
from typing import Optional, Dict, Any

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
    """تنفيذ موقّع على Binance (Spot/Futures) بدون تبعية خارجية إضافية."""
    def __init__(self, creds: BinanceCreds, futures: bool = False, recv_window: int = 5000, timeout: float = 8.0):
        self.creds = creds
        self.base = BINANCE_FUTU_BASE if futures else BINANCE_SPOT_BASE
        self.is_futures = futures
        self.recv_window = recv_window
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.creds.api_key})

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.recv_window
        q = urlencode(params, doseq=True)
        sig = hmac.new(self.creds.api_secret.encode(), q.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _get(self, path: str, params: Dict[str, Any] | None = None, signed: bool = False):
        url = self.base + path
        p = params.copy() if params else {}
        if signed:
            p = self._sign(p)
        return self.session.get(url, params=p, timeout=self.timeout)

    def _post(self, path: str, params: Dict[str, Any] | None = None, signed: bool = False):
        url = self.base + path
        p = params.copy() if params else {}
        if signed:
            p = self._sign(p)
        return self.session.post(url, params=p, timeout=self.timeout)

    def exchange_info(self, symbol: str) -> Dict[str, Any] | None:
        try:
            path = "/fapi/v1/exchangeInfo" if self.is_futures else "/api/v3/exchangeInfo"
            r = self._get(path, {"symbol": symbol.upper()}, signed=False)
            if r.ok:
                data = r.json()
                symbols = data.get("symbols") or []
                return symbols[0] if symbols else None
            log.warning("exchangeInfo failed %s: %s", symbol, r.text[:200])
        except Exception as e:
            log.warning("exchangeInfo error: %s", e)
        return None

    def account_balance(self) -> float | None:
        """Spot: free USDT؛ Futures: availableBalance."""
        try:
            if self.is_futures:
                r = self._get("/fapi/v2/balance", signed=True)
                if r.ok:
                    for it in r.json():
                        if it.get("asset") == "USDT":
                            return float(it.get("availableBalance", 0))
            else:
                r = self._get("/api/v3/account", signed=True)
                if r.ok:
                    for it in r.json().get("balances", []):
                        if it.get("asset") == "USDT":
                            return float(it.get("free", 0))
            log.warning("account_balance failed: %s", r.text[:200])
        except Exception as e:
            log.warning("account_balance error: %s", e)
        return None

    def place_order(
        self, *, symbol: str, side: str, order_type: str, quantity: float,
        price: float | None = None, time_in_force: str = "GTC", reduce_only: bool = False
    ) -> OrderResult:
        try:
            symbol = symbol.upper()
            params = {
                "symbol": symbol,
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
            r = self._post(path, params, signed=True)
            if r.ok:
                return OrderResult(ok=True, payload=r.json())
            return OrderResult(ok=False, payload={}, message=r.text)
        except Exception as e:
            return OrderResult(ok=False, payload={}, message=str(e))
# --- END OF FILE ---