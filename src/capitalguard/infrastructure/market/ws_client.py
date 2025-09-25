# src/capitalguard/infrastructure/market/ws_client.py (v20.0.1 - Fixed)
"""
Binance WebSocket client with enhanced reliability and reconnection logic.
"""

import asyncio
import json
import logging
import time
from typing import List, Optional, Dict, Any
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

log = logging.getLogger(__name__)

class BinanceWS:
    """WebSocket client مع إصلاحات شاملة"""
    BASE = "wss://stream.binance.com:9443"

    async def combined_stream(self, symbols: List[str], handler):
        """اتصال WebSocket مع إدارة متقدمة للأخطاء"""
        if not symbols:
            log.warning("⚠️ No symbols provided to combined_stream.")
            return

        streams = [f"{s.lower()}@kline_1s" for s in symbols]
        stream_path = "/stream?streams=" + "/".join(streams)
        full_uri = f"{self.BASE}{stream_path}"

        log.info("🔌 Connecting to WebSocket for %d symbols: %s", len(symbols), symbols)

        reconnect_attempt = 0
        max_reconnect_attempts = 10
        
        while reconnect_attempt < max_reconnect_attempts:
            try:
                async with websockets.connect(
                    full_uri, 
                    ping_interval=20, 
                    ping_timeout=10,
                    close_timeout=10,
                    max_size=2**20  # 1MB max message size
                ) as ws:
                    
                    log.info("✅ Successfully connected to Binance WebSocket")
                    reconnect_attempt = 0  # Reset counter on successful connection
                    
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
                            else:
                                log.warning("⚠️ Invalid price data for %s: L=%f H=%f", symbol, low_price, high_price)

                        except (json.JSONDecodeError, KeyError, ValueError) as e:
                            log.warning("❌ Failed to parse WebSocket message: %s - Message: %s", e, msg[:200])
                        except Exception as e:
                            log.error("❌ Error in message handler: %s", e)

            except (ConnectionClosed, ConnectionClosedError, ConnectionClosedOK) as e:
                reconnect_attempt += 1
                wait_time = min(2 ** reconnect_attempt, 60)  # Exponential backoff
                
                log.warning("🔌 WebSocket connection closed (attempt %d/%d). Reconnecting in %ds: %s", 
                           reconnect_attempt, max_reconnect_attempts, wait_time, e)
                
                if reconnect_attempt >= max_reconnect_attempts:
                    log.critical("💥 Max reconnection attempts reached. Giving up.")
                    break
                    
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                reconnect_attempt += 1
                wait_time = min(2 ** reconnect_attempt, 30)
                
                log.error("❌ WebSocket error (attempt %d/%d). Retrying in %ds: %s", 
                         reconnect_attempt, max_reconnect_attempts, wait_time, e)
                
                if reconnect_attempt >= max_reconnect_attempts:
                    log.critical("💥 Max reconnection attempts reached. Giving up.")
                    break
                    
                await asyncio.sleep(wait_time)

        log.error("🛑 WebSocket client stopped after %d attempts.", reconnect_attempt)