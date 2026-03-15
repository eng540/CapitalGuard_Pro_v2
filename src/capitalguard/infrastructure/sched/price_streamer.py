#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---
# File: src/capitalguard/infrastructure/sched/price_streamer.py
# Version: v10.0.0-EVENT-DRIVEN-GLOBAL
# ✅ THE FIX: Added global Event Trigger so any service can wake it up without direct coupling.

import asyncio
import logging
from typing import Set, Dict, Optional
from capitalguard.infrastructure.market.ws_client import BinanceWSClient
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.db.models import RecommendationStatusEnum, UserTradeStatusEnum, Recommendation, UserTrade
from capitalguard.infrastructure.core_engine import core_cache

log = logging.getLogger(__name__)

# 🔥 كائن الحدث العالمي (Global Event)
_global_update_event = None

def get_global_update_event() -> asyncio.Event:
    global _global_update_event
    if _global_update_event is None:
        _global_update_event = asyncio.Event()
    return _global_update_event

def trigger_price_update():
    """وظيفة عامة يمكن استدعاؤها من أي ملف لإيقاظ المحرك فوراً"""
    try:
        ev = get_global_update_event()
        ev.set()
    except Exception as e:
        log.error(f"Failed to trigger global price update: {e}")

class PriceStreamer:
    def __init__(self, price_queue: asyncio.Queue, repo: RecommendationRepository):
        self.price_queue = price_queue
        self.repo = repo
        self.client = BinanceWSClient()
        self._watcher_task: Optional[asyncio.Task] = None
        self._running = False

    def trigger_update(self):
        trigger_price_update()

    def start(self, loop=None):
        if self._running: return
        self._running = True
        
        # التأكد من تهيئة الحدث في الـ Loop الصحيح
        get_global_update_event()
        
        if loop:
            loop.create_task(self.client.start(self._handle_price))
            self._watcher_task = loop.create_task(self._watch_db_loop())
        else:
            asyncio.create_task(self.client.start(self._handle_price))
            self._watcher_task = asyncio.create_task(self._watch_db_loop())
            
        log.info("PriceStreamer (Global Event-Driven Mode) started with Zero CPU idle.")

    def stop(self):
        self._running = False
        trigger_price_update() # للإيقاظ بغرض الإغلاق
        if self._watcher_task: self._watcher_task.cancel()
        asyncio.create_task(self.client.stop())

    async def _get_symbols_to_watch(self) -> Dict[str, Set[str]]:
        try:
            loop = asyncio.get_running_loop()
            def fetch_from_db():
                with session_scope() as session:
                    recs = session.query(Recommendation).filter(
                        Recommendation.status.in_([RecommendationStatusEnum.ACTIVE, RecommendationStatusEnum.PENDING])
                    ).all()
                    trades = session.query(UserTrade).filter(
                        UserTrade.status.in_([UserTradeStatusEnum.ACTIVATED, UserTradeStatusEnum.PENDING_ACTIVATION])
                    ).all()

                    res_map = {"Futures": set(), "Spot": set()}
                    for r in recs:
                        m = "Spot" if r.market and "spot" in r.market.lower() else "Futures"
                        if r.asset: res_map[m].add(r.asset.upper())
                    for t in trades:
                        if t.asset: res_map["Futures"].add(t.asset.upper())
                    return res_map
            return await loop.run_in_executor(None, fetch_from_db)
        except Exception as e:
            log.error(f"Symbol DB fetch error: {e}")
            return {"Futures": set(), "Spot": set()}

    async def _handle_price(self, symbol: str, low: float, high: float, close: float):
        await self.price_queue.put({
            "symbol": symbol, "market": "Futures", "low": low, "high": high, "close": close,
            "ts": int(asyncio.get_event_loop().time())
        })
        try:
            await core_cache.set(f"price:FUTURES:{symbol}", close, ttl=60)
            await core_cache.set(f"price:SPOT:{symbol}", close, ttl=60)
        except:
            pass

    async def _watch_db_loop(self):
        """مراقب نائم لا يستهلك الموارد، يستيقظ فقط عند استدعاء trigger_price_update"""
        last_symbols = set()
        ev = get_global_update_event()
        
        # تفعيل أول مرة لجلب الصفقات المفتوحة مسبقاً
        trigger_price_update()

        while self._running:
            try:
                # 💤 نوم عميق (0% CPU)
                await ev.wait()
                ev.clear() 
                
                if not self._running: break

                symbols_map = await self._get_symbols_to_watch()
                current_symbols = symbols_map["Futures"] | symbols_map["Spot"]
                
                if current_symbols != last_symbols:
                    log.info(f"🔄 WOKE UP! Updating Live Subscriptions to: {current_symbols}")
                    await self.client.update_subscriptions(list(current_symbols))
                    last_symbols = current_symbols
                    
            except Exception as e:
                log.error(f"Fatal Error in PriceStreamer Event loop: {e}", exc_info=True)
                await asyncio.sleep(5) 

#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---