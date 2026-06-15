import requests

BASE = "https://api.binance.com/api/v3"

class BinanceClient:
    def get_price(self, symbol: str) -> float:
        r = requests.get(f"{BASE}/ticker/price", params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data["price"])
