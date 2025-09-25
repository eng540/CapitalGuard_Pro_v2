# src/capitalguard/infrastructure/market/ws_client.py (v19.0.5)
"""
Binance WebSocket client with enhanced logging.
"""

import asyncio
import json
import logging
from typing import List, Optional, Dict, Any
import websockets

log = logging.getLogger(__name__)

class BinanceWebSocketClient:
    """Client for Binance WebSocket API."""
    
    def __init__(self):
        self.websocket = None
        self.connected = False
        self.uri = "wss://stream.binance.com:9443/ws"
        self._message_count = 0

    async def connect(self, symbols: List[str]):
        """Connects to Binance WebSocket for the given symbols."""
        if not symbols:
            log.warning("No symbols provided for WebSocket connection")
            return
            
        try:
            # إنشاء stream name للرموز
            streams = [f"{symbol.lower()}@kline_1s" for symbol in symbols]
            stream_param = "/".join(streams)
            full_uri = f"{self.uri}/{stream_param}"
            
            log.info("Connecting to combined 1s K-line WebSocket stream for %d symbols: %s", 
                    len(symbols), symbols)
            
            self.websocket = await websockets.connect(full_uri, ping_interval=20, ping_timeout=10)
            self.connected = True
            log.info("✅ Successfully connected to Binance combined K-line stream")
            
        except Exception as e:
            log.error("Failed to connect to Binance WebSocket: %s", e)
            self.connected = False
            raise

    async def receive_message(self) -> Optional[Dict[str, Any]]:
        """Receives a message from the WebSocket."""
        if not self.connected or not self.websocket:
            log.warning("WebSocket not connected")
            return None
            
        try:
            message = await asyncio.wait_for(self.websocket.recv(), timeout=30.0)
            self._message_count += 1
            
            if self._message_count % 100 == 0:  # تسجيل كل 100 رسالة
                log.debug("WebSocket message %d received: %s...", self._message_count, message[:100])
            
            return json.loads(message)
            
        except asyncio.TimeoutError:
            log.warning("WebSocket receive timeout")
            return None
        except websockets.exceptions.ConnectionClosed:
            log.error("WebSocket connection closed")
            self.connected = False
            return None
        except Exception as e:
            log.error("Error receiving WebSocket message: %s", e)
            return None

    def disconnect(self):
        """Disconnects from the WebSocket."""
        if self.websocket:
            asyncio.create_task(self.websocket.close())
        self.connected = False
        log.info("WebSocket disconnected. Total messages received: %d", self._message_count)

    async def health_check(self) -> bool:
        """Performs a health check on the WebSocket connection."""
        if not self.connected or not self.websocket:
            return False
        try:
            # محاولة إرسال ping للتحقق من الاتصال
            pong = await asyncio.wait_for(self.websocket.ping(), timeout=5.0)
            return pong is not None
        except:
            self.connected = False
            return False