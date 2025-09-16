# --- START OF DIAGNOSTIC, MODIFIED FILE (v_ws_keepalive_test) ---
# src/capitalguard/infrastructure/market/ws_client.py

import asyncio
import json
import websockets
import logging
from typing import List

log = logging.getLogger(__name__)

class BinanceWS:
    """
    A robust WebSocket client for Binance, optimized for efficiency.
    It uses a single combined stream to subscribe to multiple tickers,
    which is the best practice for performance and resource management.
    """
    BASE = "wss://stream.binance.com:9443"

    async def combined_stream(self, symbols: List[str], handler):
        """
        Connects to a single combined stream for multiple symbols.
        This is vastly more efficient than opening one connection per symbol.

        Args:
            symbols (List[str]): A list of symbols to subscribe to (e.g., ["BTCUSDT", "ETHUSDT"]).
            handler: An async function to be called with (symbol, price, raw_data) on each update.
        """
        if not symbols:
            log.warning("No symbols provided to combined_stream, returning.")
            return

        streams = [f"{s.lower()}@miniTicker" for s in symbols]
        stream_path = "/stream?streams=" + "/".join(streams)
        url = f"{self.BASE}{stream_path}"
        
        log.info(f"Connecting to combined WebSocket stream for {len(symbols)} symbols.")

        try:
            # ✅ DIAGNOSTIC CHANGE: Reduced ping interval and timeout from 20s to 10s.
            # This is to test the hypothesis that an intermediate network device (e.g., NAT gateway in the cloud environment)
            # is dropping the connection due to perceived inactivity. By sending pings more frequently,
            # we keep the connection "active" in the eyes of the network hardware.
            async with websockets.connect(url, ping_interval=10, ping_timeout=10) as ws:
                log.info("Successfully connected to Binance combined stream.")
                async for msg in ws:
                    try:
                        payload = json.loads(msg)
                        
                        data = payload.get("data")
                        if not data:
                            continue

                        symbol = data.get("s", "").upper()
                        price = float(data.get("c", 0.0))
                        
                        if symbol and price > 0:
                            await handler(symbol, price, data)

                    except json.JSONDecodeError:
                        log.warning("Failed to decode WebSocket JSON message: %s", msg)
                    except Exception:
                        log.exception("An error occurred in the WebSocket message handler.")
        
        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"WebSocket connection closed unexpectedly: {e}. Will be reconnected by the watcher.")
            raise
        except Exception:
            log.exception("A critical error occurred in the WebSocket client.")
            raise
# --- END OF DIAGNOSTIC, MODIFIED FILE (v_ws_keepalive_test) ---