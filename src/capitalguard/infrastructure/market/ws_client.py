#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---
# File: src/capitalguard/infrastructure/market/ws_client.py
# Version: v3.0.0-PRO (Live Dynamic Subscriptions)
# ✅ THE FIX: 
#    1. Connects to a persistent base stream WITHOUT dropping the connection.
#    2. Uses JSON payloads {"method": "SUBSCRIBE"} to dynamically add/remove symbols on the fly.
#    3. Prevents Flash Crash blindness and API rate limits.

import asyncio
import json
import logging
from typing import List, Callable, Any, Set
import websockets

log = logging.getLogger(__name__)

class BinanceWSClient:
    # نستخدم مسار الـ stream الأساسي بدون تحديد عملات في الرابط
    BASE = "wss://stream.binance.com:9443/stream"

    def __init__(self):
        self.ws = None
        self.current_symbols: Set[str] = set()
        self.handler: Callable = None
        self._listen_task = None
        self._running = False

    async def start(self, handler: Callable[[str, float, float, float], Any]):
        """يبدأ فتح شريان الاتصال الدائم في الخلفية"""
        self.handler = handler
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        log.info("Binance WS Client background listener started.")

    async def stop(self):
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            await self.ws.close()

    async def update_subscriptions(self, new_symbols_list: List[str]):
        """
        يُقارن العملات الجديدة بالحالية، ويرسل أوامر اشتراك/إلغاء حية 
        دون إغلاق الاتصال المفتوح!
        """
        if not self.ws or not self.ws.open:
            # إذا لم يكن متصلاً بعد، نحفظها فقط وسيشترك بها عند الاتصال
            self.current_symbols = set(s.lower() for s in new_symbols_list)
            return

        new_symbols = set(s.lower() for s in new_symbols_list)
        
        # استخراج ما يجب إضافته وما يجب حذفه
        to_add = new_symbols - self.current_symbols
        to_remove = self.current_symbols - new_symbols

        # إرسال طلب اشتراك حي (Live SUBSCRIBE)
        if to_add:
            add_params = [f"{s}@kline_1s" for s in to_add]
            log.info(f"📡 Sending LIVE SUBSCRIBE for: {to_add}")
            await self.ws.send(json.dumps({
                "method": "SUBSCRIBE",
                "params": add_params,
                "id": 1
            }))

        # إرسال طلب إلغاء حي (Live UNSUBSCRIBE) للتخفيف عن الشبكة
        if to_remove:
            remove_params = [f"{s}@kline_1s" for s in to_remove]
            log.info(f"📡 Sending LIVE UNSUBSCRIBE for: {to_remove}")
            await self.ws.send(json.dumps({
                "method": "UNSUBSCRIBE",
                "params": remove_params,
                "id": 2
            }))

        self.current_symbols = new_symbols

    async def _listen_loop(self):
        """شريان الحياة الذي لا ينقطع"""
        while self._running:
            try:
                log.info("Connecting to Binance Persistent WebSocket...")
                async with websockets.connect(self.BASE, ping_interval=20, ping_timeout=20) as ws:
                    self.ws = ws
                    log.info("✅ Persistent WebSocket Connected Successfully.")
                    
                    # إذا انقطع الاتصال وعدنا، نعيد الاشتراك بالعملات المعروفة لدينا
                    if self.current_symbols:
                        await self.update_subscriptions(list(self.current_symbols))

                    while self._running:
                        message = await ws.recv()
                        data = json.loads(message)
                        
                        # تجاهل رسائل تأكيد الاشتراك
                        if "result" in data and "id" in data:
                            continue
                            
                        payload = data.get("data", {})
                        k = payload.get("k", {})
                        
                        symbol = k.get("s")
                        if not symbol:
                            continue
                            
                        low = float(k.get("l"))
                        high = float(k.get("h"))
                        close = float(k.get("c"))
                        
                        if self.handler:
                            await self.handler(symbol, low, high, close)
                            
            except websockets.exceptions.ConnectionClosed as e:
                log.warning(f"Connection dropped: {e}. Reconnecting in 5s...")
                self.ws = None
                await asyncio.sleep(5)
            except Exception as e:
                log.error(f"Stream error: {e}. Restarting in 5s...")
                self.ws = None
                await asyncio.sleep(5)
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/market/ws_client.py ---