# src/capitalguard/infrastructure/market/ws_client.py (v20.0.0 - Production Ready)
"""
Binance WebSocket client with enhanced logging and reliability features.
"""

import asyncio
import json
import logging
from typing import List, Optional, Dict, Any
import websockets

log = logging.getLogger(__name__)

# ✅ --- FIX: Renamed class to BinanceWS for consistency with PriceStreamer ---
class BinanceWS:
    """A robust WebSocket client for Binance, optimized for reliability."""
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
        full_uri = f"{self.BASE}{stream_path}" # ✅ Use full_uri here

        log.info(f"Connecting to combined 1s K-line WebSocket stream for {len(symbols)} symbols.")

        try:
            async with websockets.connect(full_uri, ping_interval=20, ping_timeout=10) as ws:
                log.info("✅ Successfully connected to Binance combined K-line stream")
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