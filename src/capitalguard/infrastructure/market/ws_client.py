#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---
# src/capitalguard/infrastructure/market/ws_client.py
# Version: v2.0.0 - Multi-Exchange Support
# ✅ THE FIX: Added BybitWS class alongside BinanceWS.
# 🎯 IMPACT: Enables multi-source price streaming.

import asyncio
import json
import websockets
import logging
from typing import List, Dict, Any

log = logging.getLogger(__name__)

class BinanceWS:
    """
    Binance WebSocket Client (1s K-line stream).
    """
    BASE = "wss://stream.binance.com:9443"

    async def combined_stream(self, symbols: List[str], handler):
        if not symbols: return

        # Normalize symbols for Binance (lower case)
        streams = [f"{s.lower()}@kline_1s" for s in symbols]
        stream_path = "/stream?streams=" + "/".join(streams)
        url = f"{self.BASE}{stream_path}"
        
        log.info(f"🔌 Binance: Connecting to stream for {len(symbols)} symbols...")

        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                log.info("✅ Binance: Connected.")
                async for msg in ws:
                    try:
                        payload = json.loads(msg)
                        data = payload.get("data")
                        if not data or data.get('e') != 'kline': continue

                        kline_data = data.get('k')
                        symbol = kline_data.get("s", "").upper()
                        # Use Close price as the main price point
                        price = float(kline_data.get("c", 0.0))
                        
                        if symbol and price > 0:
                            await handler(symbol, price, price) # Send Price twice as (Low, High) approximation

                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue
                    except Exception:
                        continue
        
        except Exception as e:
            log.error(f"❌ Binance WS Error: {e}")
            raise # Re-raise to let the streamer handle reconnection

class BybitWS:
    """
    Bybit WebSocket Client (V5 Linear API).
    """
    # V5 Public Linear Endpoint
    BASE = "wss://stream.bybit.com/v5/public/linear"

    async def stream(self, symbols: List[str], handler):
        if not symbols: return

        # Normalize symbols for Bybit (Uppercase, typically BTCUSDT matches)
        # Topic format: tickers.{symbol}
        topics = [f"tickers.{s.upper()}" for s in symbols]
        
        # Limit topics per connection if necessary (Bybit limit is high, usually fine)
        # Construct subscription message
        sub_msg = {
            "op": "subscribe",
            "args": topics
        }

        log.info(f"🔌 Bybit: Connecting to stream for {len(symbols)} symbols...")

        try:
            async with websockets.connect(self.BASE, ping_interval=None) as ws:
                log.info("✅ Bybit: Connected.")
                
                # Send Subscription
                await ws.send(json.dumps(sub_msg))
                
                # Start Heartbeat Loop (Bybit requires explicit application-level ping)
                ping_task = asyncio.create_task(self._heartbeat_loop(ws))

                try:
                    async for msg in ws:
                        try:
                            payload = json.loads(msg)
                            
                            # Handle Pong
                            if payload.get("op") == "pong":
                                continue
                                
                            # Handle Subscription Confirmation
                            if payload.get("op") == "subscribe":
                                if payload.get("success"):
                                    log.info("✅ Bybit: Subscription successful.")
                                continue

                            # Handle Ticker Data
                            topic = payload.get("topic", "")
                            data = payload.get("data", {})
                            
                            if "tickers" in topic and data:
                                # Extract symbol from topic or data
                                symbol = topic.split(".")[-1]
                                last_price = data.get("lastPrice")
                                
                                if last_price:
                                    price = float(last_price)
                                    # Send to main system
                                    await handler(symbol, price, price)

                        except (json.JSONDecodeError, ValueError):
                            continue
                        except Exception as e:
                            log.error(f"Bybit processing error: {e}")
                            
                finally:
                    ping_task.cancel()

        except Exception as e:
            log.error(f"❌ Bybit WS Error: {e}")
            raise # Allow reconnection logic to trigger

    async def _heartbeat_loop(self, ws):
        """Sends a ping every 20 seconds to keep Bybit connection alive."""
        while True:
            try:
                await asyncio.sleep(20)
                await ws.send(json.dumps({"op": "ping"}))
                # log.debug("Bybit: Ping sent")
            except Exception:
                break
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---