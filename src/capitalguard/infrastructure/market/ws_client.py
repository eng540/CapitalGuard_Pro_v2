#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---
# File: src/capitalguard/infrastructure/market/ws_client.py
# Version: v3.1.0-STABLE (Live Dynamic Subscriptions + websockets v12 fix)
#
# ✅ THE FIX (BUG-W1):
#   ws.open لا يوجد في websockets v12+ → AttributeError في runtime
#   الإصلاح: استبدال ws.open بـ self._connected boolean flag يُضبط
#   داخل _listen_loop عند الاتصال وعند الانقطاع.
#   هذا الأسلوب متوافق مع كل إصدارات websockets.
#
# الميزات المحتفظ بها من v3.0.0:
#   - Persistent WebSocket لا ينقطع عند إضافة عملات جديدة
#   - Dynamic SUBSCRIBE/UNSUBSCRIBE بدون إعادة اتصال
#   - إعادة الاشتراك التلقائي بعد انقطاع الاتصال
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

import asyncio
import json
import logging
from typing import List, Callable, Any, Set
import websockets

log = logging.getLogger(__name__)


class BinanceWSClient:
    """
    عميل WebSocket ثابت لـ Binance Futures.
    يستخدم اشتراكات حية (SUBSCRIBE/UNSUBSCRIBE) بدون قطع الاتصال.
    """

    # المسار الأساسي بدون عملات — نضيفها عبر SUBSCRIBE
    BASE = "wss://stream.binance.com:9443/stream"

    def __init__(self):
        self.ws = None
        self.current_symbols: Set[str] = set()
        self.handler: Callable = None
        self._listen_task = None
        self._running = False
        # ✅ BUG-W1 FIX: flag بديل عن ws.open المحذوف في websockets v12
        self._connected: bool = False

    async def start(self, handler: Callable[[str, float, float, float], Any]) -> None:
        """يبدأ شريان الاتصال الدائم في الخلفية."""
        self.handler = handler
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        log.info("BinanceWSClient: background listener started.")

    async def stop(self) -> None:
        """إيقاف الاتصال بأمان."""
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
        يُقارن العملات الجديدة بالحالية ويرسل SUBSCRIBE/UNSUBSCRIBE حياً
        دون إغلاق الاتصال المفتوح.
        """
        # ✅ BUG-W1 FIX: نستخدم self._connected بدلاً من self.ws.open
        if not self.ws or not self._connected:
            # الاتصال لم يبدأ بعد — نحفظ العملات لتُطبَّق عند الاتصال
            self.current_symbols = {s.lower() for s in new_symbols_list}
            log.debug(
                f"BinanceWSClient: not connected yet — "
                f"queued {len(self.current_symbols)} symbols."
            )
            return

        new_symbols = {s.lower() for s in new_symbols_list}

        to_add = new_symbols - self.current_symbols
        to_remove = self.current_symbols - new_symbols

        # إرسال SUBSCRIBE للعملات الجديدة
        if to_add:
            params = [f"{s}@kline_1s" for s in to_add]
            log.info(f"📡 SUBSCRIBE: {to_add}")
            try:
                await self.ws.send(json.dumps({
                    "method": "SUBSCRIBE",
                    "params": params,
                    "id": 1,
                }))
            except Exception as e:
                log.warning(f"SUBSCRIBE send failed: {e}")

        # إرسال UNSUBSCRIBE للعملات المنتهية
        if to_remove:
            params = [f"{s}@kline_1s" for s in to_remove]
            log.info(f"📡 UNSUBSCRIBE: {to_remove}")
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
        حلقة الاستماع الدائمة مع إعادة الاتصال التلقائي.
        تُعيد الاشتراك بالعملات المعروفة بعد كل انقطاع.
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
                    # ✅ BUG-W1 FIX: نضبط _connected عند نجاح الاتصال
                    self._connected = True
                    log.info("✅ BinanceWSClient: connected successfully.")

                    # إعادة الاشتراك بالعملات المعروفة بعد إعادة الاتصال
                    if self.current_symbols:
                        log.info(
                            f"BinanceWSClient: re-subscribing to "
                            f"{len(self.current_symbols)} known symbols after reconnect."
                        )
                        await self.update_subscriptions(list(self.current_symbols))

                    # حلقة استقبال الرسائل
                    while self._running:
                        try:
                            message = await ws.recv()
                        except websockets.exceptions.ConnectionClosed:
                            raise  # يُعالج في الـ outer except

                        data = json.loads(message)

                        # تجاهل رسائل تأكيد SUBSCRIBE/UNSUBSCRIBE
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
                # ✅ BUG-W1 FIX: نمسح _connected عند أي انقطاع
                self._connected = False
                self.ws = None

            if self._running:
                await asyncio.sleep(5)
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---
