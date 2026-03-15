#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---
# File: src/capitalguard/infrastructure/sched/price_streamer.py
# Version: v7.1.0-STABLE (Live Dynamic Subscriptions + sync stop() fix)
#
# ✅ THE FIX (BUG-W2):
#   PriceStreamer.stop() كان يستدعي asyncio.create_task(self.client.stop())
#   من sync context → RuntimeError: no running event loop
#   الإصلاح:
#     1. start() يحفظ مرجع الـ loop في self._loop
#     2. stop() يستخدم self._loop.call_soon_threadsafe() لجدولة
#        asyncio.ensure_future() بأمان من أي thread
#
# الميزات المحتفظ بها من v7.0.0:
#   - Live Dynamic Subscriptions بدون قطع الاتصال
#   - مراقبة DB كل 5 ثوانٍ
#   - تحديث Cache للـ WebApp
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-15

import asyncio
import logging
from typing import Set, Dict, Optional

from capitalguard.infrastructure.market.ws_client import BinanceWSClient
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.db.models import (
    RecommendationStatusEnum,
    UserTradeStatusEnum,
    Recommendation,
    UserTrade,
)
from capitalguard.infrastructure.core_engine import core_cache

log = logging.getLogger(__name__)


class PriceStreamer:
    """
    يُدير اتصال WebSocket الدائم بـ Binance ويوزع تيكات الأسعار
    على AlertService عبر price_queue.
    """

    def __init__(self, price_queue: asyncio.Queue, repo: RecommendationRepository):
        self.price_queue = price_queue
        self.repo = repo
        self.client = BinanceWSClient()
        self._watcher_task: Optional[asyncio.Task] = None
        self._running = False
        # ✅ BUG-W2 FIX: نحفظ مرجع الـ loop لاستخدامه في stop()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """
        يبدأ شريان الاتصال الدائم ومراقب قاعدة البيانات.
        يُستدعى دائماً من داخل bg thread (AlertService._bg_runner).
        """
        if self._running:
            return
        self._running = True

        # ✅ BUG-W2 FIX: حفظ مرجع الـ loop
        if loop:
            self._loop = loop
        else:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.get_event_loop()

        if loop:
            loop.create_task(self.client.start(self._handle_price))
            self._watcher_task = loop.create_task(self._watch_db_loop())
        else:
            asyncio.create_task(self.client.start(self._handle_price))
            self._watcher_task = asyncio.create_task(self._watch_db_loop())

        log.info("PriceStreamer (Live Dynamic Mode) started.")

    def stop(self) -> None:
        """
        ✅ BUG-W2 FIX: يُوقف PriceStreamer بأمان من أي thread.
        client.stop() هي async — نجدولها في الـ bg loop عبر call_soon_threadsafe.
        """
        self._running = False

        # إلغاء مراقب DB
        if self._watcher_task:
            try:
                # إذا كنا في نفس الـ loop
                self._watcher_task.cancel()
            except Exception:
                pass

        # ✅ BUG-W2 FIX: جدولة async stop() بأمان
        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self.client.stop(),
                        loop=self._loop,
                    )
                )
            except Exception as e:
                log.warning(f"PriceStreamer.stop: could not schedule client.stop(): {e}")
        else:
            log.warning(
                "PriceStreamer.stop: bg loop not running — "
                "WebSocket may not close cleanly."
            )

        log.info("PriceStreamer stopped.")

    # ─────────────────────────────────────────────────────────────
    # DB symbol watcher
    # ─────────────────────────────────────────────────────────────

    async def _get_symbols_to_watch(self) -> Dict[str, Set[str]]:
        """جلب العملات النشطة مع كاش 5 ثوانٍ للتقاط التوصيات فوراً."""
        symbols_by_market: Dict[str, Set[str]] = {"Futures": set(), "Spot": set()}
        try:
            cached = await core_cache.get("active_watch_symbols")
            if cached:
                return cached

            with session_scope() as session:
                recs = session.query(Recommendation).filter(
                    Recommendation.status.in_([
                        RecommendationStatusEnum.ACTIVE,
                        RecommendationStatusEnum.PENDING,
                    ])
                ).all()

                trades = session.query(UserTrade).filter(
                    UserTrade.status.in_([
                        UserTradeStatusEnum.ACTIVATED,
                        UserTradeStatusEnum.PENDING_ACTIVATION,
                    ])
                ).all()

                for r in recs:
                    market = (
                        "Spot"
                        if r.market and "spot" in r.market.lower()
                        else "Futures"
                    )
                    symbols_by_market[market].add(r.asset.upper())

                for t in trades:
                    symbols_by_market["Futures"].add(t.asset.upper())

            await core_cache.set("active_watch_symbols", symbols_by_market, ttl=5)
            return symbols_by_market

        except Exception as e:
            log.error(f"PriceStreamer: symbol fetch error: {e}")
            return symbols_by_market

    async def _watch_db_loop(self) -> None:
        """
        مراقب ذكي يعمل كل 5 ثوانٍ.
        يُحدِّث اشتراكات WebSocket عند تغيير قائمة العملات النشطة.
        """
        last_symbols: Set[str] = set()

        while self._running:
            try:
                symbols_map = await self._get_symbols_to_watch()
                current_symbols = symbols_map["Futures"] | symbols_map["Spot"]

                if current_symbols != last_symbols:
                    log.info(
                        f"PriceStreamer: symbols changed "
                        f"({len(last_symbols)} → {len(current_symbols)}). "
                        "Updating live subscriptions..."
                    )
                    await self.client.update_subscriptions(list(current_symbols))
                    last_symbols = current_symbols

            except Exception as e:
                log.error(f"PriceStreamer._watch_db_loop error: {e}")

            await asyncio.sleep(5)

    # ─────────────────────────────────────────────────────────────
    # Price handler
    # ─────────────────────────────────────────────────────────────

    async def _handle_price(
        self, symbol: str, low: float, high: float, close: float
    ) -> None:
        """يُغذِّي AlertService بتيك السعر ويُحدِّث الكاش للـ WebApp."""
        # تغذية AlertService
        await self.price_queue.put({
            "symbol": symbol,
            "market": "Futures",
            "low":    low,
            "high":   high,
            "close":  close,
            "ts":     int(asyncio.get_event_loop().time()),
        })

        # تحديث كاش الأسعار للـ WebApp
        await core_cache.set(f"price:FUTURES:{symbol}", close, ttl=60)
        await core_cache.set(f"price:SPOT:{symbol}",    close, ttl=60)
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---
