# --- START OF FINAL, COMPLETE, AND PRODUCTION-READY FILE (Version 1.1.0) ---
# src/capitalguard/infrastructure/market/ws_client.py

import asyncio
import json
import websockets
import logging
from typing import List, Dict, Any

log = logging.getLogger(__name__)

class BinanceWS:
    """
    A robust WebSocket client for Binance, optimized for reliability.
    ✅ FINAL ARCHITECTURE v1.1: Switched to 1-second K-line streams (@kline_1s)
    to capture the high and low price of every second. This prevents missing
    triggers during high-volatility wicks, ensuring maximum reliability.
    """
    BASE = "wss://stream.binance.com:9443"

    async def combined_stream(self, symbols: List[str], handler):
        """
        Connects to a single combined stream for multiple symbols using 1s k-lines.

        Args:
            symbols (List[str]): A list of symbols to subscribe to.
            handler: An async function to be called with (symbol, low_price, high_price) on each update.
        """
        if not symbols:
            log.warning("No symbols provided to combined_stream, returning.")
            return

        streams = [f"{s.lower()}@kline_1s" for s in symbols]
        stream_path = "/stream?streams=" + "/".join(streams)
        url = f"{self.BASE}{stream_path}"
        
        log.info(f"Connecting to combined 1s K-line WebSocket stream for {len(symbols)} symbols.")

        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                log.info("Successfully connected to Binance combined K-line stream.")
                async for msg in ws:
                    try:
                        payload = json.loads(msg)
                        
                        data = payload.get("data")
                        if not data or data.get('e') != 'kline':
                            continue

                        kline_data = data.get('k')
                        symbol = kline_data.get("s", "").upper()
                        low_price = float(kline_data.get("l", 0.0))
                        high_price = float(kline_data.get("h", 0.0))
                        
                        if symbol and low_price > 0 and high_price > 0:
                            await handler(symbol, low_price, high_price)

                    except (json.JSONDecodeError, KeyError, ValueError):
                        log.warning("Failed to parse WebSocket K-line message: %s", msg[:200])
                    except Exception:
                        log.exception("An error occurred in the WebSocket message handler.")
        
        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"WebSocket connection closed unexpectedly: {e}. Will be reconnected by the streamer.")
            raise
        except Exception:
            log.exception("A critical error occurred in the WebSocket client.")
            raise

# --- END OF FINAL, COMPLETE, AND PRODUCTION-READY FILE (Version 1.1.0) ---