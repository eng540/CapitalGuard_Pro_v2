#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---
# File: src/capitalguard/infrastructure/sched/price_streamer.py
# Version: v7.0.0-PROFESSIONAL
# ✅ THE FIX:
#    1. Uses Live WebSocket Subscriptions (No disconnects on symbol changes).
#    2. Checks DB every 5 seconds and smoothly pushes new symbols to the WS client.

import asyncio
import logging
from typing import Set, Dict, Optional
from capitalguard.infrastructure.market.ws_client import BinanceWSClient
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.db.models import RecommendationStatusEnum, UserTradeStatusEnum, Recommendation, UserTrade
from capitalguard.infrastructure.core_engine import core_cache

log = logging.getLogger(__name__)

class PriceStreamer:
    def __init__(self, price_queue: asyncio.Queue, repo: RecommendationRepository):
        self.price_queue = price_queue
        self.repo = repo
        self.client = BinanceWSClient() # النسخة الجديدة الداعمة للاشتراك الحي
        self._watcher_task: Optional[asyncio.Task] = None
        self._running = False

    def start(self, loop=None):
        if self._running: return
        self._running = True
        
        # 1. نبدأ شريان الاتصال الدائم
        if loop:
            loop.create_task(self.client.start(self._handle_price))
            self._watcher_task = loop.create_task(self._watch_db_loop())
        else:
            asyncio.create_task(self.client.start(self._handle_price))
            self._watcher_task = asyncio.create_task(self._watch_db_loop())
            
        log.info("PriceStreamer (Live Dynamic Mode) started.")

    def stop(self):
        self._running = False
        if self._watcher_task: self._watcher_task.cancel()
        asyncio.create_task(self.client.stop())

    async def _get_symbols_to_watch(self) -> Dict[str, Set[str]]:
        """جلب العملات النشطة من قاعدة البيانات مع كاش سريع جداً"""
        symbols_by_market = {"Futures": set(), "Spot": set()}
        try:
            cached = await core_cache.get("active_watch_symbols")
            if cached: return cached

            with session_scope() as session:
                recs = session.query(Recommendation).filter(
                    Recommendation.status.in_([RecommendationStatusEnum.ACTIVE, RecommendationStatusEnum.PENDING])
                ).all()
                
                trades = session.query(UserTrade).filter(
                    UserTrade.status.in_([UserTradeStatusEnum.ACTIVATED, UserTradeStatusEnum.PENDING_ACTIVATION])
                ).all()

                for r in recs:
                    m = "Spot" if r.market and "spot" in r.market.lower() else "Futures"
                    symbols_by_market[m].add(r.asset.upper())
                
                for t in trades:
                    symbols_by_market["Futures"].add(t.asset.upper())

            # الكاش 5 ثوانٍ فقط لتلقط التوصيات فوراً
            await core_cache.set("active_watch_symbols", symbols_by_market, ttl=5)
            return symbols_by_market
        except Exception as e:
            log.error(f"Symbol fetch error: {e}")
            return symbols_by_market

    async def _handle_price(self, symbol: str, low: float, high: float, close: float):
        # تغذية خدمة التنبيهات وإيقاف الخسارة (Alert Service)
        await self.price_queue.put({
            "symbol": symbol, 
            "market": "Futures", 
            "low": low, 
            "high": high, 
            "close": close,
            "ts": int(asyncio.get_event_loop().time())
        })
        
        # تحديث الكاش لتطبيق الويب (WebApp)
        await core_cache.set(f"price:FUTURES:{symbol}", close, ttl=60)
        await core_cache.set(f"price:SPOT:{symbol}", close, ttl=60)

    async def _watch_db_loop(self):
        """مراقب ذكي يعمل كل 5 ثوانٍ لتغذية عميل الاتصال دون قطعه"""
        last_symbols = set()
        while self._running:
            try:
                symbols_map = await self._get_symbols_to_watch()
                current_symbols = symbols_map["Futures"] | symbols_map["Spot"]
                
                # إذا تغيرت القائمة، نأمر عميل الشبكة بتحديث اشتراكه حياً
                if current_symbols != last_symbols:
                    log.info(f"Database symbols changed. Activating Live Subscriptions...")
                    await self.client.update_subscriptions(list(current_symbols))
                    last_symbols = current_symbols
                    
            except Exception as e:
                log.error(f"Error in DB Watcher loop: {e}")
                
            await asyncio.sleep(5)
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---