import asyncio, json, websockets

class BinanceWS:
    BASE = "wss://stream.binance.com:9443/ws"

    async def mini_ticker(self, symbol: str, handler):
        s = symbol.lower()
        stream = f"{s}@miniTicker"
        url = f"{self.BASE}/{stream}"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            async for msg in ws:
                try:
                    data = json.loads(msg)
                    # miniTicker field 'c' is close price
                    price = float(data.get("c") or data.get("C") or 0.0)
                    if price:
                        await handler(symbol.upper(), price, data)
                except Exception:
                    continue
