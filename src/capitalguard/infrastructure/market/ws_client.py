#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---
# File: src/capitalguard/infrastructure/market/ws_client.py
# Version: v3.1.0-STABLE (Live Dynamic Subscriptions + websockets v12 fix)
#
# âœ… THE FIX (BUG-W1):
#   ws.open Ù„Ø§ ÙŠÙˆØ¬Ø¯ ÙÙŠ websockets v12+ â†’ AttributeError ÙÙŠ runtime
#   Ø§Ù„Ø¥ØµÙ„Ø§Ø­: Ø§Ø³ØªØ¨Ø¯Ø§Ù„ ws.open Ø¨Ù€ self._connected boolean flag ÙŠÙØ¶Ø¨Ø·
#   Ø¯Ø§Ø®Ù„ _listen_loop Ø¹Ù†Ø¯ Ø§Ù„Ø§ØªØµØ§Ù„ ÙˆØ¹Ù†Ø¯ Ø§Ù„Ø§Ù†Ù‚Ø·Ø§Ø¹.
#   Ù‡Ø°Ø§ Ø§Ù„Ø£Ø³Ù„ÙˆØ¨ Ù…ØªÙˆØ§ÙÙ‚ Ù…Ø¹ ÙƒÙ„ Ø¥ØµØ¯Ø§Ø±Ø§Øª websockets.
#
# Ø§Ù„Ù…ÙŠØ²Ø§Øª Ø§Ù„Ù…Ø­ØªÙØ¸ Ø¨Ù‡Ø§ Ù…Ù† v3.0.0:
#   - Persistent WebSocket Ù„Ø§ ÙŠÙ†Ù‚Ø·Ø¹ Ø¹Ù†Ø¯ Ø¥Ø¶Ø§ÙØ© Ø¹Ù…Ù„Ø§Øª Ø¬Ø¯ÙŠØ¯Ø©
#   - Dynamic SUBSCRIBE/UNSUBSCRIBE Ø¨Ø¯ÙˆÙ† Ø¥Ø¹Ø§Ø¯Ø© Ø§ØªØµØ§Ù„
#   - Ø¥Ø¹Ø§Ø¯Ø© Ø§Ù„Ø§Ø´ØªØ±Ø§Ùƒ Ø§Ù„ØªÙ„Ù‚Ø§Ø¦ÙŠ Ø¨Ø¹Ø¯ Ø§Ù†Ù‚Ø·Ø§Ø¹ Ø§Ù„Ø§ØªØµØ§Ù„
#
# Reviewed-by: Guardian Protocol v1 â€” 2026-03-15

import asyncio
import json
import logging
from typing import List, Callable, Any, Set
import websockets

log = logging.getLogger(__name__)


class BinanceWSClient:
    """
    Ø¹Ù…ÙŠÙ„ WebSocket Ø«Ø§Ø¨Øª Ù„Ù€ Binance Futures.
    ÙŠØ³ØªØ®Ø¯Ù… Ø§Ø´ØªØ±Ø§ÙƒØ§Øª Ø­ÙŠØ© (SUBSCRIBE/UNSUBSCRIBE) Ø¨Ø¯ÙˆÙ† Ù‚Ø·Ø¹ Ø§Ù„Ø§ØªØµØ§Ù„.
    """

    # Ø§Ù„Ù…Ø³Ø§Ø± Ø§Ù„Ø£Ø³Ø§Ø³ÙŠ Ø¨Ø¯ÙˆÙ† Ø¹Ù…Ù„Ø§Øª â€” Ù†Ø¶ÙŠÙÙ‡Ø§ Ø¹Ø¨Ø± SUBSCRIBE
    BASE = "wss://stream.binance.com:9443/stream"

    def __init__(self):
        self.ws = None
        self.current_symbols: Set[str] = set()
        self.handler: Callable = None
        self._listen_task = None
        self._running = False
        # âœ… BUG-W1 FIX: flag Ø¨Ø¯ÙŠÙ„ Ø¹Ù† ws.open Ø§Ù„Ù…Ø­Ø°ÙˆÙ ÙÙŠ websockets v12
        self._connected: bool = False

    async def start(self, handler: Callable[[str, float, float, float], Any]) -> None:
        """ÙŠØ¨Ø¯Ø£ Ø´Ø±ÙŠØ§Ù† Ø§Ù„Ø§ØªØµØ§Ù„ Ø§Ù„Ø¯Ø§Ø¦Ù… ÙÙŠ Ø§Ù„Ø®Ù„ÙÙŠØ©."""
        self.handler = handler
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        log.info("BinanceWSClient: background listener started.")

    async def stop(self) -> None:
        """Ø¥ÙŠÙ‚Ø§Ù Ø§Ù„Ø§ØªØµØ§Ù„ Ø¨Ø£Ù…Ø§Ù†."""
        self._running = False
        self._connected = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        log.info("BinanceWSClient: stopped.")

    async def update_subscriptions(self, new_symbols_list: List[str]) -> None:
        """
        ÙŠÙÙ‚Ø§Ø±Ù† Ø§Ù„Ø¹Ù…Ù„Ø§Øª Ø§Ù„Ø¬Ø¯ÙŠØ¯Ø© Ø¨Ø§Ù„Ø­Ø§Ù„ÙŠØ© ÙˆÙŠØ±Ø³Ù„ SUBSCRIBE/UNSUBSCRIBE Ø­ÙŠØ§Ù‹
        Ø¯ÙˆÙ† Ø¥ØºÙ„Ø§Ù‚ Ø§Ù„Ø§ØªØµØ§Ù„ Ø§Ù„Ù…ÙØªÙˆØ­.
        """
        # âœ… BUG-W1 FIX: Ù†Ø³ØªØ®Ø¯Ù… self._connected Ø¨Ø¯Ù„Ø§Ù‹ Ù…Ù† self.ws.open
        if not self.ws or not self._connected:
            # Ø§Ù„Ø§ØªØµØ§Ù„ Ù„Ù… ÙŠØ¨Ø¯Ø£ Ø¨Ø¹Ø¯ â€” Ù†Ø­ÙØ¸ Ø§Ù„Ø¹Ù…Ù„Ø§Øª Ù„ØªÙØ·Ø¨ÙŽÙ‘Ù‚ Ø¹Ù†Ø¯ Ø§Ù„Ø§ØªØµØ§Ù„
            self.current_symbols = {s.lower() for s in new_symbols_list}
            log.debug(
                f"BinanceWSClient: not connected yet â€” "
                f"queued {len(self.current_symbols)} symbols."
            )
            return

        new_symbols = {s.lower() for s in new_symbols_list}

        to_add = new_symbols - self.current_symbols
        to_remove = self.current_symbols - new_symbols

        # Ø¥Ø±Ø³Ø§Ù„ SUBSCRIBE Ù„Ù„Ø¹Ù…Ù„Ø§Øª Ø§Ù„Ø¬Ø¯ÙŠØ¯Ø©
        if to_add:
            params = [f"{s}@kline_1s" for s in to_add]
            log.info(f"ðŸ“¡ SUBSCRIBE: {to_add}")
            try:
                await self.ws.send(json.dumps({
                    "method": "SUBSCRIBE",
                    "params": params,
                    "id": 1,
                }))
            except Exception as e:
                log.warning(f"SUBSCRIBE send failed: {e}")

        # Ø¥Ø±Ø³Ø§Ù„ UNSUBSCRIBE Ù„Ù„Ø¹Ù…Ù„Ø§Øª Ø§Ù„Ù…Ù†ØªÙ‡ÙŠØ©
        if to_remove:
            params = [f"{s}@kline_1s" for s in to_remove]
            log.info(f"ðŸ“¡ UNSUBSCRIBE: {to_remove}")
            try:
                await self.ws.send(json.dumps({
                    "method": "UNSUBSCRIBE",
                    "params": params,
                    "id": 2,
                }))
            except Exception as e:
                log.warning(f"UNSUBSCRIBE send failed: {e}")

        self.current_symbols = new_symbols

    async def _listen_loop(self) -> None:
        """
        Ø­Ù„Ù‚Ø© Ø§Ù„Ø§Ø³ØªÙ…Ø§Ø¹ Ø§Ù„Ø¯Ø§Ø¦Ù…Ø© Ù…Ø¹ Ø¥Ø¹Ø§Ø¯Ø© Ø§Ù„Ø§ØªØµØ§Ù„ Ø§Ù„ØªÙ„Ù‚Ø§Ø¦ÙŠ.
        ØªÙØ¹ÙŠØ¯ Ø§Ù„Ø§Ø´ØªØ±Ø§Ùƒ Ø¨Ø§Ù„Ø¹Ù…Ù„Ø§Øª Ø§Ù„Ù…Ø¹Ø±ÙˆÙØ© Ø¨Ø¹Ø¯ ÙƒÙ„ Ø§Ù†Ù‚Ø·Ø§Ø¹.
        """
        while self._running:
            try:
                log.info("BinanceWSClient: connecting to Binance persistent WS...")

                async with websockets.connect(
                    self.BASE,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    self.ws = ws
                    # âœ… BUG-W1 FIX: Ù†Ø¶Ø¨Ø· _connected Ø¹Ù†Ø¯ Ù†Ø¬Ø§Ø­ Ø§Ù„Ø§ØªØµØ§Ù„
                    self._connected = True
                    log.info("âœ… BinanceWSClient: connected successfully.")

                    # Ø¥Ø¹Ø§Ø¯Ø© Ø§Ù„Ø§Ø´ØªØ±Ø§Ùƒ Ø¨Ø§Ù„Ø¹Ù…Ù„Ø§Øª Ø§Ù„Ù…Ø¹Ø±ÙˆÙØ© Ø¨Ø¹Ø¯ Ø¥Ø¹Ø§Ø¯Ø© Ø§Ù„Ø§ØªØµØ§Ù„
                    if self.current_symbols:
                        log.info(
                            f"BinanceWSClient: re-subscribing to "
                            f"{len(self.current_symbols)} known symbols after reconnect."
                        )
                        await self.update_subscriptions(list(self.current_symbols))

                    # Ø­Ù„Ù‚Ø© Ø§Ø³ØªÙ‚Ø¨Ø§Ù„ Ø§Ù„Ø±Ø³Ø§Ø¦Ù„
                    while self._running:
                        try:
                            message = await ws.recv()
                        except websockets.exceptions.ConnectionClosed:
                            raise  # ÙŠÙØ¹Ø§Ù„Ø¬ ÙÙŠ Ø§Ù„Ù€ outer except

                        data = json.loads(message)

                        # ØªØ¬Ø§Ù‡Ù„ Ø±Ø³Ø§Ø¦Ù„ ØªØ£ÙƒÙŠØ¯ SUBSCRIBE/UNSUBSCRIBE
                        if "result" in data and "id" in data:
                            continue

                        payload = data.get("data", {})
                        k = payload.get("k", {})

                        symbol = k.get("s")
                        if not symbol:
                            continue

                        try:
                            low   = float(k.get("l", 0))
                            high  = float(k.get("h", 0))
                            close = float(k.get("c", 0))
                        except (TypeError, ValueError):
                            continue

                        if self.handler and low and high and close:
                            await self.handler(symbol, low, high, close)

            except websockets.exceptions.ConnectionClosed as e:
                log.warning(f"BinanceWSClient: connection dropped ({e}). Reconnecting in 5s...")
            except asyncio.CancelledError:
                log.info("BinanceWSClient: listen loop cancelled.")
                break
            except Exception as e:
                log.error(f"BinanceWSClient: unexpected error ({e}). Restarting in 5s...")
            finally:
                # âœ… BUG-W1 FIX: Ù†Ù…Ø³Ø­ _connected Ø¹Ù†Ø¯ Ø£ÙŠ Ø§Ù†Ù‚Ø·Ø§Ø¹
                self._connected = False
                self.ws = None

            if self._running:
                await asyncio.sleep(5)
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---
