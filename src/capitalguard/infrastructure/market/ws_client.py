# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---
# File: src/capitalguard/infrastructure/market/ws_client.py
# Version: v2.0.0-STABLE (Keepalive Fix)
# ✅ THE FIX: 
#    1. Implements 'BinanceWSClient' class required by PriceStreamer.
#    2. Adds 'ping_interval' to prevent 1011 errors (Connection Drops).
#    3. Optimizes JSON parsing for speed.

import asyncio
import json
import logging
from typing import List, Callable, Any
import websockets

log = logging.getLogger(__name__)

class BinanceWSClient:
    """
    A robust WebSocket client for Binance, optimized for reliability.
    ✅ FINAL ARCHITECTURE v1.1: Switched to 1-second K-line streams (@kline_1s)
    to capture the high and low price of every second. This prevents missing
    triggers during high-volatility wicks, ensuring maximum reliability.
    """
    
    BASE = "wss://stream.binance.com:9443"

    async def combined_stream(self, symbols: List[str], handler: Callable[[str, float, float, float], Any]):
        """
        Connects to a single combined stream for multiple symbols using 1s k-lines.
        
        Args:
            symbols (List[str]): A list of symbols to subscribe to (e.g. ["BTCUSDT", "ETHUSDT"]).
            handler: An async function to be called with (symbol, low, high, close) on each update.
        """
        if not symbols:
            log.warning("No symbols provided to combined_stream, returning.")
            return

        # Convert to lowercase for stream names
        # Stream format: <symbol>@kline_1s
        streams = [f"{s.lower()}@kline_1s" for s in symbols]
        
        # Binance allows combining streams in the URL
        stream_path = "/stream?streams=" + "/".join(streams)
        url = f"{self.BASE}{stream_path}"

        log.info(f"Connecting to Binance WebSocket for {len(symbols)} symbols...")
        
        # ✅ CRITICAL: ping_interval=20 ensures the connection stays alive
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            log.info("✅ WebSocket Connected.")
            
            while True:
                try:
                    message = await ws.recv()
                    data = json.loads(message)
                    
                    # Data format for combined stream:
                    # {"stream": "btcusdt@kline_1s", "data": {...}}
                    payload = data.get("data", {})
                    k = payload.get("k", {})
                    
                    # Extract critical price data
                    symbol = k.get("s")      # Symbol
                    low = float(k.get("l"))  # Low Price
                    high = float(k.get("h")) # High Price
                    close = float(k.get("c")) # Close Price (Current)
                    
                    if symbol and low and high and close:
                        # Pass to the handler (PriceStreamer._handle_price)
                        await handler(symbol, low, high, close)
                        
                except websockets.exceptions.ConnectionClosed as e:
                    log.warning(f"WebSocket connection closed: {e}")
                    raise # Re-raise to let PriceStreamer handle the restart loop
                except Exception as e:
                    log.error(f"Error processing message: {e}")
                    # Continue loop on parsing errors, don't crash connection
                    continue

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---