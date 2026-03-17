#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---
# File: src/capitalguard/infrastructure/sched/price_streamer.py
# Version: v8.0.0-PUBSUB
#
# ✅ THE UPGRADE (P1 — Redis Pub/Sub بدل Polling):
#
#   المشكلة في v7.x:
#     _watch_db_loop() كان يستعلم DB كل 5 ثوانٍ:
#       "هل توجد رموز جديدة؟" — حتى لو لا شيء تغيّر
#     هذا polling مكلف ومزعج.
#
#   الحل — طبقتان بدلاً من polling:
#
#   الطبقة 1: Initial Load (مرة واحدة عند startup)
#     يجلب كل الرموز النشطة من DB مرة واحدة فقط
#     يُشترك بها في Binance WS
#
#   الطبقة 2: Redis Pub/Sub (event-driven)
#     يستمع لـ channel "cg:symbol_update"
#     عند نشر توصية جديدة: creation_service يُرسل رسالة
#     PriceStreamer يستيقظ فوراً ويُشترك بالرمز الجديد
#     لا polling — لا DB queries دورية
#
#   الطبقة 3: Safety Sweep (كل 5 دقائق — من الذاكرة لا DB)
#     يُزامن WS subscriptions مع active_triggers في RAM
#     لا يستعلم DB — يقرأ فقط ما هو موجود في الذاكرة
#     يُزيل رموزاً انتهت توصياتها
#
#   النتيجة:
#     قبل: SQL query كل 5 ثوانٍ = 17,280 query/يوم
#     بعد: SQL query واحدة عند startup + 0 queries دورية
#
# ✅ محفوظ من v7.1.0:
#   - BUG-W2: self._loop + call_soon_threadsafe في stop()
#   - _handle_price() بدون تغيير
#   - interface مع AlertService بدون تغيير
#
# Reviewed-by: Guardian Protocol v1 — 2026-03-17

import asyncio
import json
import logging
import os
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

# Channel اسم قناة Redis للأحداث
SYMBOL_UPDATE_CHANNEL = "cg:symbol_update"

# فترة الـ Safety Sweep (دقائق) — من الذاكرة لا DB
SAFETY_SWEEP_INTERVAL = 300  # 5 دقائق


class PriceStreamer:
    """
    يُدير اتصال WebSocket الدائم بـ Binance ويوزع تيكات الأسعار.

    اكتشاف الرموز الجديدة:
      1. Initial Load: DB query واحدة عند startup
      2. Redis Pub/Sub: يستيقظ فوراً عند توصية جديدة
      3. Safety Sweep: كل 5 دقائق من الذاكرة (بدون DB)
    """

    def __init__(self, price_queue: asyncio.Queue, repo: RecommendationRepository):
        self.price_queue = price_queue
        self.repo = repo
        self.client = BinanceWSClient()

        # مجموعة الرموز المُشترَك بها حالياً
        self._subscribed_symbols: Set[str] = set()

        # مرجع لـ active_triggers في AlertService (يُعيَّن من الخارج)
        # يُستخدم في Safety Sweep لمعرفة الرموز النشطة بدون DB
        self.active_triggers_ref: Optional[Dict] = None

        self._pubsub_task: Optional[asyncio.Task] = None
        self._sweep_task:  Optional[asyncio.Task] = None
        self._running = False

        # ✅ FIX BUG-W2: نحفظ مرجع الـ loop لاستخدامه في stop()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_active_triggers_ref(self, triggers_dict: Dict) -> None:
        """
        يُمرِّر مرجع active_triggers من AlertService.
        يُستخدم في Safety Sweep لمعرفة الرموز النشطة بدون DB.
        يُستدعى من AlertService._bg_runner() بعد إنشاء PriceStreamer.
        """
        self.active_triggers_ref = triggers_dict

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """يبدأ WebSocket + Initial Load + Pub/Sub listener + Safety Sweep."""
        if self._running:
            return
        self._running = True

        # ✅ FIX BUG-W2
        if loop:
            self._loop = loop
        else:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.get_event_loop()

        if loop:
            loop.create_task(self.client.start(self._handle_price))
            loop.create_task(self._startup_sequence())
        else:
            asyncio.create_task(self.client.start(self._handle_price))
            asyncio.create_task(self._startup_sequence())

        log.info("PriceStreamer v8 (Pub/Sub mode) started.")

    def stop(self) -> None:
        """✅ FIX BUG-W2: إيقاف آمن من أي thread."""
        self._running = False

        for task in (self._pubsub_task, self._sweep_task):
            if task:
                try:
                    task.cancel()
                except Exception:
                    pass

        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self.client.stop(), loop=self._loop,
                    )
                )
            except Exception as e:
                log.warning("PriceStreamer.stop: %s", e)
        else:
            log.warning("PriceStreamer.stop: bg loop not running.")

        log.info("PriceStreamer stopped.")

    # ─────────────────────────────────────────────────────────────
    # Startup sequence
    # ─────────────────────────────────────────────────────────────

    async def _startup_sequence(self) -> None:
        """
        1. انتظر قليلاً حتى يكتمل اتصال WS
        2. جلب الرموز من DB (مرة واحدة)
        3. ابدأ الاستماع لـ Pub/Sub
        4. ابدأ Safety Sweep
        """
        await asyncio.sleep(2)  # انتظر WS يتصل

        # Initial Load من DB
        await self._load_initial_symbols()

        # Pub/Sub listener
        self._pubsub_task = asyncio.ensure_future(
            self._listen_symbol_events()
        )

        # Safety Sweep من الذاكرة
        self._sweep_task = asyncio.ensure_future(
            self._safety_sweep_loop()
        )

    # ─────────────────────────────────────────────────────────────
    # Initial Load — DB query واحدة عند startup
    # ─────────────────────────────────────────────────────────────

    async def _load_initial_symbols(self) -> None:
        """
        يجلب كل الرموز النشطة من DB مرة واحدة عند startup.
        بعد هذه اللحظة لا توجد DB queries دورية.
        """
        symbols: Set[str] = set()
        try:
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
                    if r.asset:
                        symbols.add(r.asset.upper())
                for t in trades:
                    if t.asset:
                        symbols.add(t.asset.upper())

            if symbols:
                await self.client.update_subscriptions(list(symbols))
                self._subscribed_symbols = symbols
                log.info(
                    "PriceStreamer: initial load — subscribed to %d symbols: %s",
                    len(symbols), symbols,
                )
            else:
                log.info("PriceStreamer: no active symbols at startup.")

        except Exception as e:
            log.error("PriceStreamer: initial load failed: %s", e)

    # ─────────────────────────────────────────────────────────────
    # Redis Pub/Sub listener — event-driven
    # ─────────────────────────────────────────────────────────────

    async def _listen_symbol_events(self) -> None:
        """
        يستمع لـ Redis channel "cg:symbol_update".
        يستيقظ فوراً عند نشر توصية جديدة بدون أي polling.

        إذا Redis غير متاح → يُعيد المحاولة كل 30 ثانية.
        """
        log.info("PriceStreamer: Pub/Sub listener starting...")

        while self._running:
            try:
                url = os.getenv("REDIS_URL")
                if not url:
                    log.info(
                        "PriceStreamer: No REDIS_URL — "
                        "Pub/Sub unavailable. Safety Sweep will handle updates."
                    )
                    return  # Safety Sweep تكفي كشبكة أمان

                try:
                    import redis.asyncio as aioredis
                except ImportError:
                    log.warning("PriceStreamer: redis.asyncio not available.")
                    return

                client = aioredis.from_url(url, decode_responses=True)

                async with client.pubsub() as pubsub:
                    await pubsub.subscribe(SYMBOL_UPDATE_CHANNEL)
                    log.info(
                        "PriceStreamer: subscribed to Redis channel '%s'.",
                        SYMBOL_UPDATE_CHANNEL,
                    )

                    async for message in pubsub.listen():
                        if not self._running:
                            break

                        if message.get("type") != "message":
                            continue

                        try:
                            data = json.loads(message.get("data", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            continue

                        action = data.get("action")
                        symbol = (data.get("symbol") or "").upper()

                        if not symbol:
                            continue

                        if action == "ADD" and symbol not in self._subscribed_symbols:
                            await self.client.update_subscriptions(
                                list(self._subscribed_symbols | {symbol})
                            )
                            self._subscribed_symbols.add(symbol)
                            log.info(
                                "PriceStreamer: [Pub/Sub] subscribed to new symbol: %s",
                                symbol,
                            )

                        elif action == "REMOVE":
                            await self._maybe_unsubscribe(symbol)

            except asyncio.CancelledError:
                log.info("PriceStreamer: Pub/Sub listener cancelled.")
                break
            except Exception as e:
                log.error(
                    "PriceStreamer: Pub/Sub error: %s — retrying in 30s.", e
                )
                await asyncio.sleep(30)

    async def _maybe_unsubscribe(self, symbol: str) -> None:
        """
        يُلغي الاشتراك من رمز فقط إذا لم يعد له triggers نشطة.
        يستخدم active_triggers_ref بدلاً من DB.
        """
        if self.active_triggers_ref is None:
            return
        # فحص: هل لا يزال الرمز في active_triggers؟
        still_needed = any(
            key.startswith(f"{symbol}:")
            for key in self.active_triggers_ref.keys()
        )
        if not still_needed and symbol in self._subscribed_symbols:
            remaining = self._subscribed_symbols - {symbol}
            await self.client.update_subscriptions(list(remaining))
            self._subscribed_symbols.discard(symbol)
            log.info(
                "PriceStreamer: [Pub/Sub] unsubscribed from %s (no active triggers).",
                symbol,
            )

    # ─────────────────────────────────────────────────────────────
    # Safety Sweep — كل 5 دقائق من الذاكرة
    # ─────────────────────────────────────────────────────────────

    async def _safety_sweep_loop(self) -> None:
        """
        يُزامن WS subscriptions مع active_triggers كل 5 دقائق.
        يقرأ من الذاكرة فقط — صفر DB queries.
        دوره: تصحيح أي تباين ناتج عن فقدان رسالة Pub/Sub.
        """
        while self._running:
            await asyncio.sleep(SAFETY_SWEEP_INTERVAL)
            try:
                if self.active_triggers_ref is None:
                    continue

                # الرموز النشطة حالياً في الذاكرة
                needed: Set[str] = set()
                for key in self.active_triggers_ref.keys():
                    symbol = key.split(":")[0]
                    if symbol:
                        needed.add(symbol)

                if needed != self._subscribed_symbols:
                    log.info(
                        "PriceStreamer: [Safety Sweep] syncing subscriptions "
                        "(%d needed, %d subscribed).",
                        len(needed), len(self._subscribed_symbols),
                    )
                    await self.client.update_subscriptions(list(needed))
                    self._subscribed_symbols = needed

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("PriceStreamer: Safety Sweep error: %s", e)

    # ─────────────────────────────────────────────────────────────
    # Price handler — بدون تغيير
    # ─────────────────────────────────────────────────────────────

    async def _handle_price(
        self, symbol: str, low: float, high: float, close: float
    ) -> None:
        """يُغذِّي AlertService بتيك السعر ويُحدِّث الكاش للـ WebApp."""
        await self.price_queue.put({
            "symbol": symbol,
            "market": "Futures",
            "low":    low,
            "high":   high,
            "close":  close,
            "ts":     int(asyncio.get_event_loop().time()),
        })
        await core_cache.set(f"price:FUTURES:{symbol}", close, ttl=60)
        await core_cache.set(f"price:SPOT:{symbol}",    close, ttl=60)

#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/sched/price_streamer.py ---
