# src/capitalguard/application/services/alert_service.py v20.0.0 (with Invalidation Logic)
"""
AlertService — Now with proactive invalidation of PENDING recommendations.
"""

import logging
import asyncio
import threading
from typing import List, Dict, Any, Optional, Set
from contextlib import suppress
import time
import re

from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer

log = logging.getLogger(__name__)


class AlertService:
    def __init__(self, trade_service, repo: RecommendationRepository, streamer: Optional[PriceStreamer] = None, debounce_seconds: float = 1.0):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)
        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()
        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_processed: Dict[int, Dict[str, float]] = {}
        self._debounce_seconds = float(debounce_seconds)
        self._tp_re = re.compile(r"^TP(\d+)$", flags=re.IGNORECASE)

    async def build_triggers_index(self):
        log.info("Attempting to build in-memory trigger index...")
        retry_delay = 5
        while True:
            try:
                with session_scope() as session:
                    trigger_data = self.repo.list_all_active_triggers_data(session)
                break
            except Exception:
                log.critical("CRITICAL: DB read failure. Retrying in %ds...", retry_delay, exc_info=True)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        for item in trigger_data:
            try:
                asset_raw = (item.get("asset") or "").strip().upper()
                if not asset_raw:
                    log.warning("Skipping trigger with empty asset: %s", item)
                    continue
                item["asset"] = asset_raw
                self._add_item_to_trigger_dict(new_triggers, item)
            except Exception:
                log.exception("Failed processing trigger item: %s", item)
        async with self._triggers_lock:
            self.active_triggers = new_triggers
        total_recs = len(trigger_data) if trigger_data is not None else 0
        log.info("✅ Trigger index built: %d recommendations across %d symbols.", total_recs, len(new_triggers))

    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        asset = (item.get("asset") or "").strip().upper()
        if not asset: raise ValueError("Empty asset")
        if asset not in trigger_dict: trigger_dict[asset] = []
        rec_id = item.get("id")
        processed_events = item.get("processed_events", set())
        def _create_trigger(trigger_type: str, price: Any) -> Dict[str, Any]:
            return {"rec_id": rec_id, "user_id": item.get("user_id"), "side": item.get("side"), "type": trigger_type, "price": float(price), "order_type": item.get("order_type"), "processed_events": processed_events, "status": item.get("status")}
        status = item.get("status")
        status_norm = status.name if hasattr(status, "name") else str(status).upper()
        if status_norm == "PENDING":
            trigger_dict[asset].append(_create_trigger("ENTRY", item.get("entry")))
            if item.get("stop_loss") is not None:
                trigger_dict[asset].append(_create_trigger("SL", item.get("stop_loss")))
            return
        if status_norm == "ACTIVE":
            if item.get("stop_loss") is not None: trigger_dict[asset].append(_create_trigger("SL", item.get("stop_loss")))
            if item.get("profit_stop_price") is not None: trigger_dict[asset].append(_create_trigger("PROFIT_STOP", item.get("profit_stop_price")))
            for idx, target in enumerate(item.get("targets", []), 1):
                trigger_dict[asset].append(_create_trigger(f"TP{idx}", target.get("price")))

    async def update_triggers_for_recommendation(self, rec_id: int):
        log.debug("Updating triggers for Rec #%s in memory.", rec_id)
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if not self.active_triggers[symbol]: del self.active_triggers[symbol]
        try:
            with session_scope() as session:
                item = self.repo.get_active_trigger_data_by_id(session, rec_id)
        except Exception:
            log.exception("Failed fetching active trigger data for rec %s", rec_id)
            return
        if not item:
            log.debug("No active trigger found for rec %s.", rec_id)
            return
        asset = (item.get("asset") or "").strip().upper()
        item["asset"] = asset
        async with self._triggers_lock:
            try:
                self._add_item_to_trigger_dict(self.active_triggers, item)
                log.info("Updated triggers for Rec #%s in memory under symbol %s.", rec_id, asset)
            except Exception:
                log.exception("Failed to add updated trigger for rec %s", rec_id)

    async def remove_triggers_for_recommendation(self, rec_id: int):
        async with self._triggers_lock:
            for symbol in list(self.active_triggers.keys()):
                before_len = len(self.active_triggers.get(symbol, []))
                self.active_triggers[symbol] = [t for t in self.active_triggers.get(symbol, []) if t.get("rec_id") != rec_id]
                if len(self.active_triggers.get(symbol, [])) < before_len:
                    log.info("Removed triggers for Rec #%s from symbol %s in memory.", rec_id, symbol)
                if not self.active_triggers.get(symbol):
                    self.active_triggers.pop(symbol, None)

    async def add_processed_event_in_memory(self, rec_id: int, event_type: str):
        async with self._triggers_lock:
            for symbol, triggers in self.active_triggers.items():
                for trigger in triggers:
                    if trigger.get("rec_id") == rec_id:
                        trigger["processed_events"].add(event_type)
                        log.debug("Added event '%s' to in-memory state for Rec #%s", event_type, rec_id)

    async def _run_index_sync(self, interval_seconds: int = 300):
        log.info("Index sync task started (interval=%ss).", interval_seconds)
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                await self.build_triggers_index()
        except asyncio.CancelledError:
            log.info("Index sync task cancelled.")
        except Exception:
            log.exception("Index sync encountered error.")

    async def _process_queue(self):
        log.info("AlertService queue processor started.")
        try:
            while True:
                symbol, low_price, high_price = await self.price_queue.get()
                try:
                    await self.check_and_process_alerts(symbol, low_price, high_price)
                except Exception:
                    log.exception("Error while processing alerts for %s", symbol)
                finally:
                    with suppress(Exception): self.price_queue.task_done()
        except asyncio.CancelledError:
            log.info("Queue processor cancelled.")
        except Exception:
            log.exception("Unexpected error in queue processor.")

    def start(self):
        try:
            loop = asyncio.get_running_loop()
            if self._processing_task is None or self._processing_task.done(): self._processing_task = loop.create_task(self._process_queue())
            if self._index_sync_task is None or self._index_sync_task.done(): self._index_sync_task = loop.create_task(self._run_index_sync())
            if hasattr(self.streamer, "start"): self.streamer.start()
            log.info("AlertService started in existing event loop.")
            return
        except RuntimeError:
            if self._bg_thread and self._bg_thread.is_alive():
                log.warning("AlertService background thread already running.")
                return
            def _bg_runner():
                try:
                    loop = asyncio.new_event_loop()
                    self._bg_loop = loop
                    asyncio.set_event_loop(loop)
                    self._processing_task = loop.create_task(self._process_queue())
                    self._index_sync_task = loop.create_task(self._run_index_sync())
                    if hasattr(self.streamer, "start"): self.streamer.start()
                    loop.run_forever()
                except Exception:
                    log.exception("AlertService background runner crashed.")
                finally:
                    if self._bg_loop:
                        for t in (self._processing_task, self._index_sync_task):
                            if t and not t.done(): self._bg_loop.call_soon_threadsafe(t.cancel)
                        self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
            self._bg_thread.start()
            log.info("AlertService started in background thread.")

    def stop(self):
        if hasattr(self.streamer, "stop"): self.streamer.stop()
        if self._processing_task and not self._processing_task.done(): self._processing_task.cancel()
        if self._index_sync_task and not self._index_sync_task.done(): self._index_sync_task.cancel()
        if self._bg_loop and self._bg_thread:
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            self._bg_thread.join(timeout=5.0)
        self._bg_thread = self._bg_loop = self._processing_task = self._index_sync_task = None
        log.info("AlertService stopped and cleaned up.")

    def _is_price_condition_met(self, side: str, low_price: float, high_price: float, target_price: float, condition_type: str, order_type: Optional[Any] = None) -> bool:
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()
        if side_upper == "LONG":
            if cond.startswith("TP"): return high_price >= target_price
            if cond in ("SL", "PROFIT_STOP"): return low_price <= target_price
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                if ot.endswith("LIMIT"): return low_price <= target_price
                if ot.endswith("STOP_MARKET"): return high_price >= target_price
                return low_price <= target_price or high_price >= target_price
        if side_upper == "SHORT":
            if cond.startswith("TP"): return low_price <= target_price
            if cond in ("SL", "PROFIT_STOP"): return high_price >= target_price
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                if ot.endswith("LIMIT"): return high_price >= target_price
                if ot.endswith("STOP_MARKET"): return low_price <= target_price
                return low_price <= target_price or high_price >= target_price
        return False

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        async with self._triggers_lock:
            triggers_for_symbol = list(self.active_triggers.get((symbol or "").upper(), []))
        if not triggers_for_symbol: return
        triggers_for_symbol.sort(key=lambda t: t.get("type") != "ENTRY")
        now_ts = time.time()
        for trigger in triggers_for_symbol:
            rec_id = int(trigger.get("rec_id", 0))
            ttype_raw = (trigger.get("type") or "").upper()
            execution_price = float(trigger.get("price", 0.0))
            processed_events: Set[str] = trigger.get("processed_events", set())
            status_in_memory = trigger.get("status")
            event_key = ttype_raw
            if self._tp_re.match(ttype_raw):
                m = self._tp_re.match(ttype_raw)
                event_key = f"TP{m.group(1)}_HIT"
            if event_key in processed_events: continue
            if not self._is_price_condition_met(trigger.get("side"), low_price, high_price, execution_price, ttype_raw, trigger.get("order_type")): continue
            last_map = self._last_processed.setdefault(rec_id, {})
            if last_ts := last_map.get(ttype_raw):
                if (now_ts - last_ts) < self._debounce_seconds: continue
            last_map[ttype_raw] = now_ts
            log.info("Trigger HIT for Rec #%s: Type=%s, Symbol=%s, Range=[%s,%s], Target=%s", rec_id, ttype_raw, symbol, low_price, high_price, execution_price)
            try:
                if status_in_memory == RecommendationStatus.PENDING and ttype_raw == "SL":
                    log.warning("Invalidation HIT for PENDING Rec #%s: SL hit before entry.", rec_id)
                    await self.trade_service.process_invalidation_event(rec_id)
                    continue
                if ttype_raw == "ENTRY": await self.trade_service.process_activation_event(rec_id)
                elif self._tp_re.match(ttype_raw):
                    m = self._tp_re.match(ttype_raw)
                    idx = int(m.group(1)) if m else 1
                    await self.trade_service.process_tp_hit_event(rec_id, trigger.get("user_id"), idx, execution_price)
                elif ttype_raw == "SL": await self.trade_service.process_sl_hit_event(rec_id, trigger.get("user_id"), execution_price)
                elif ttype_raw == "PROFIT_STOP": await self.trade_service.process_profit_stop_hit_event(rec_id, trigger.get("user_id"), execution_price)
                await self.add_processed_event_in_memory(rec_id, event_key)
            except Exception:
                log.exception("Failed to process and commit event for rec #%s, type %s. Will retry.", rec_id, ttype_raw)```

---

### **الملف 3 (مُحدَّث): `src/capitalguard/interfaces/telegram/keyboards.py`**

**التغييرات الرئيسية:**
*   `analyst_control_panel_keyboard` الآن تعرض أزرارًا مختلفة إذا كانت التوصية `PENDING`.

```python
# src/capitalguard/interfaces/telegram/keyboards.py (v14.0.0 - with Cancellation Button)
# --- START OF FINAL, COMPLETE, AND UX-FIXED FILE ---

import math
from typing import List, Iterable, Set, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

ITEMS_PER_PAGE = 8

def main_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي (/new)", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع (/rec)", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي (/editor)", callback_data="method_editor")],
    ])

async def build_open_recs_keyboard(items: List[Recommendation], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    keyboard: List[List[InlineKeyboardButton]] = []
    total_items = len(items)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE) if total_items else 1
    start_index = (current_page - 1) * ITEMS_PER_PAGE
    paginated_items = items[start_index: start_index + ITEMS_PER_PAGE]
    for rec in paginated_items:
        display_id = getattr(rec, "analyst_rec_id", rec.id) or rec.id
        button_text = f"#{display_id} - {rec.asset.value} ({rec.side.value})"
        if rec.status == RecommendationStatus.PENDING:
            status_icon = "⏳"
            button_text = f"{status_icon} {button_text} | معلقة"
        elif rec.status == RecommendationStatus.ACTIVE:
            live_price = await price_service.get_cached_price(rec.asset.value, rec.market)
            if live_price is not None:
                pnl = _pct(rec.entry.value, float(live_price), rec.side.value)
                status_icon = "🟢" if pnl >= 0 else "🔴"
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            else:
                status_icon = "▶️"
                button_text = f"{status_icon} {button_text} | نشطة"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"rec:show_panel:{rec.id}")])
    nav_buttons: List[List[InlineKeyboardButton]] = []
    page_nav_row: List[InlineKeyboardButton] = []
    if current_page > 1: page_nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"open_nav:page:{current_page - 1}"))
    if total_pages > 1: page_nav_row.append(InlineKeyboardButton(f"صفحة {current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages: page_nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"open_nav:page:{current_page + 1}"))
    if page_nav_row: nav_buttons.append(page_nav_row)
    keyboard.extend(nav_buttons)
    return InlineKeyboardMarkup(keyboard)

def public_channel_keyboard(rec_id: int, bot_username: str) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton("🔄 تحديث البيانات الحية", callback_data=f"rec:update_public:{rec_id}")]
    if bot_username:
        buttons.insert(0, InlineKeyboardButton("📊 تتبّع الإشارة", url=f"https://t.me/{bot_username}?start=track_{rec_id}"))
    return InlineKeyboardMarkup([buttons])

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    rec_id = rec.id
    if rec.status == RecommendationStatus.PENDING:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تحديث السعر", callback_data=f"rec:update_private:{rec.id}")],
            [InlineKeyboardButton("❌ إلغاء التوصية", callback_data=f"rec:cancel_pending:{rec_id}")],
            [InlineKeyboardButton("⬅️ العودة للقائمة", callback_data=f"open_nav:page:1")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث السعر", callback_data=f"rec:update_private:{rec.id}"), InlineKeyboardButton("✏️ تعديل", callback_data=f"rec:edit_menu:{rec.id}")],
        [InlineKeyboardButton("📈 استراتيجية الخروج", callback_data=f"rec:strategy_menu:{rec.id}"), InlineKeyboardButton("💰 جني ربح جزئي", callback_data=f"rec:close_partial:{rec.id}")],
        [InlineKeyboardButton("❌ إغلاق كلي", callback_data=f"rec:close_menu:{rec.id}")],
        [InlineKeyboardButton("⬅️ العودة لقائمة التوصيات", callback_data=f"open_nav:page:1")],
    ])

def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق الآن", callback_data=f"rec:close_market:{rec_id}")],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=f"rec:close_manual:{rec_id}")],
        [InlineKeyboardButton("⬅️ إلغاء", callback_data=f"rec:back_to_main:{rec_id}")],
    ])

def analyst_edit_menu_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 تعديل الوقف", callback_data=f"rec:edit_sl:{rec_id}"), InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:edit_tp:{rec_id}")],
        [InlineKeyboardButton("⬅️ العودة للوحة التحكم", callback_data=f"rec:back_to_main:{rec_id}")],
    ])

def build_exit_strategy_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    rec_id = rec.id
    current_strategy = rec.exit_strategy
    auto_close_text = "🎯 الإغلاق عند الهدف الأخير"
    if current_strategy == ExitStrategy.CLOSE_AT_FINAL_TP: auto_close_text = f"✅ {auto_close_text}"
    manual_close_text = "✍️ الإغلاق اليدوي فقط"
    if current_strategy == ExitStrategy.MANUAL_CLOSE_ONLY: manual_close_text = f"✅ {manual_close_text}"
    keyboard = [
        [InlineKeyboardButton(auto_close_text, callback_data=f"rec:set_strategy:{rec_id}:{ExitStrategy.CLOSE_AT_FINAL_TP.value}")],
        [InlineKeyboardButton(manual_close_text, callback_data=f"rec:set_strategy:{rec_id}:{ExitStrategy.MANUAL_CLOSE_ONLY.value}")],
        [InlineKeyboardButton("🛡️ وضع/تعديل وقف الربح", callback_data=f"rec:set_profit_stop:{rec_id}")],
    ]
    if getattr(rec, "profit_stop_price", None) is not None:
        keyboard.append([InlineKeyboardButton("🗑️ إزالة وقف الربح", callback_data=f"rec:set_profit_stop:{rec_id}:remove")])
    keyboard.append([InlineKeyboardButton("⬅️ العودة للوحة التحكم", callback_data=f"rec:back_to_main:{rec_id}")])
    return InlineKeyboardMarkup(keyboard)

def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"), InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}")]])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard_layout = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    keyboard_layout.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard_layout)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"LONG / {current_market}", callback_data=f"side_LONG"), InlineKeyboardButton(f"SHORT / {current_market}", callback_data=f"side_SHORT")],
        [InlineKeyboardButton(f"🔄 تغيير السوق (الحالي: {current_market})", callback_data="change_market_menu")],
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Futures", callback_data="market_Futures"), InlineKeyboardButton("Spot", callback_data="market_Spot")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="market_back")],
    ])

def order_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Market (دخول فوري)", callback_data="type_MARKET")],
        [InlineKeyboardButton("Limit (انتظار سعر أفضل)", callback_data="type_LIMIT")],
        [InlineKeyboardButton("Stop Market (دخول بعد اختراق)", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر في القنوات الفعّالة", callback_data=f"rec:publish:{review_token}")],
        [InlineKeyboardButton("📢 اختيار القنوات", callback_data=f"rec:choose_channels:{review_token}"), InlineKeyboardButton("📝 إضافة/تعديل ملاحظات", callback_data=f"rec:add_notes:{review_token}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{review_token}")],
    ])

def build_channel_picker_keyboard(review_token: str, channels: Iterable[dict], selected_ids: Set[int], page: int = 1, per_page: int = 5) -> InlineKeyboardMarkup:
    ch_list = list(channels)
    total = len(ch_list)
    page = max(page, 1)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = ch_list[start:end]
    rows: List[List[InlineKeyboardButton]] = []
    for ch in page_items:
        tg_chat_id = int(ch.telegram_channel_id)
        label = ch.title or (f"@{ch.username}" if ch.username else str(tg_chat_id))
        mark = "✅" if tg_chat_id in selected_ids else "☑️"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"pubsel:toggle:{review_token}:{tg_chat_id}:{page}")])
    nav: List[InlineKeyboardButton] = []
    max_page = max(1, math.ceil(total / per_page))
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"pubsel:nav:{review_token}:{page-1}"))
    if max_page > 1: nav.append(InlineKeyboardButton(f"صفحة {page}/{max_page}", callback_data="noop"))
    if page < max_page: nav.append(InlineKeyboardButton("➡️", callback_data=f"pubsel:nav:{review_token}:{page+1}"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("🚀 نشر المحدد", callback_data=f"pubsel:confirm:{review_token}"), InlineKeyboardButton("⬅️ رجوع", callback_data=f"pubsel:back:{review_token}")])
    return InlineKeyboardMarkup(rows)

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    if channel_link:
        return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)]])
    return None

def build_signal_tracking_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 نبهني عند الهدف الأول", callback_data=f"track:notify_tp1:{rec_id}"), InlineKeyboardButton("🔔 نبهني عند وقف الخسارة", callback_data=f"track:notify_sl:{rec_id}")],
        [InlineKeyboardButton("➕ أضف إلى محفظتي (قريباً)", callback_data=f"track:add_portfolio:{rec_id}")]
    ])