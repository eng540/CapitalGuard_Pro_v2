# src/capitalguard/application/services/alert_service.py (v19.1.0 - Fixed Silent Failure)
"""
AlertService v19.1.0 - الإصلاح الشامل للفشل الصامت في معالجة البيانات
"""

import logging
import asyncio
import threading
import time
import re
import copy
from typing import List, Dict, Any, Optional
from contextlib import suppress

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)
audit_log = logging.getLogger('capitalguard.audit')

class ServiceHealthMonitor:
    """مراقبة صحة الخدمة مع تحسينات شاملة"""
    def __init__(self, notifier: Any, admin_chat_id: Optional[str], main_loop: asyncio.AbstractEventLoop, stale_threshold_sec: int = 60):  # ⬅️ قللنا إلى 60 ثانية للكشف السريع
        self.last_processed_time = time.time()
        self.last_queue_size = 0
        self.stale_threshold = stale_threshold_sec
        self.notifier = notifier
        self.admin_chat_id = admin_chat_id
        self.main_loop = main_loop
        self.alert_sent = False
        self.total_processed = 0
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5

    def record_processing(self):
        """تسجيل معالجة ناجحة"""
        self.last_processed_time = time.time()
        self.total_processed += 1
        self.consecutive_errors = 0  # إعادة تعيين عداد الأخطاء
        self.alert_sent = False

    def record_error(self):
        """تسجيل خطأ في المعالجة"""
        self.consecutive_errors += 1

    def check_health(self):
        """فحص الصحة مع تحسينات"""
        current_time = time.time()
        time_since_last = current_time - self.last_processed_time
        
        # ✅ سجل حالة الصحة بشكل دوري
        if self.total_processed % 50 == 0:  # كل 50 معالجة
            log.info(f"❤️ Health status - Processed: {self.total_processed}, Last: {time_since_last:.1f}s ago, Errors: {self.consecutive_errors}")
        
        # ✅ تحذير إذا كانت أخطاء متتالية
        if self.consecutive_errors >= self.max_consecutive_errors:
            log.error(f"🚨 Consecutive errors: {self.consecutive_errors} - Possible processing issue")
        
        # ✅ تنبيه إذا توقفت المعالجة
        if time_since_last > self.stale_threshold:
            if not self.alert_sent and self.admin_chat_id and self.notifier and self.main_loop:
                log.critical(f"💥 HEALTH ALERT: No processing for {int(time_since_last)}s! Total processed: {self.total_processed}, Queue stats needed.")
                try:
                    message = f"🚨 CRITICAL: Price watcher stalled for {int(time_since_last)}s. Processed: {self.total_processed}, Errors: {self.consecutive_errors}"
                    coro = self.notifier.send_private_text(chat_id=int(self.admin_chat_id), text=message)
                    if asyncio.iscoroutine(coro):
                        asyncio.run_coroutine_threadsafe(coro, self.main_loop)
                    self.alert_sent = True
                except Exception as e:
                    log.error(f"Failed to send health alert: {e}")

class SmartDebounceManager:
    """مدير منع التكرار مع تحسينات"""
    def __init__(self, debounce_seconds: float = 1.0, max_age_seconds: float = 3600.0):
        self._events: Dict[int, Dict[str, float]] = {}
        self._debounce_seconds = debounce_seconds
        self._max_age = max_age_seconds
        self._cleanup_interval = 600
        self._cleanup_task: Optional[asyncio.Task] = None

    def start_cleanup_task(self):
        """بدء مهمة التنظيف"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            log.info("🧹 SmartDebounceManager cleanup task started.")

    async def _periodic_cleanup(self):
        """تنظيف دوري"""
        while True:
            await asyncio.sleep(self._cleanup_interval)
            now = time.time()
            cleaned_count = 0
            for rec_id, events in list(self._events.items()):
                for event_type, timestamp in list(events.items()):
                    if now - timestamp > self._max_age:
                        del self._events[rec_id][event_type]
                        cleaned_count += 1
                if not self._events[rec_id]:
                    del self._events[rec_id]
            if cleaned_count > 0:
                log.debug("🧹 DebounceManager cleaned up %d old entries.", cleaned_count)

    def is_debounced(self, rec_id: int, event_type: str) -> bool:
        """التحقق من منع التكرار"""
        now = time.time()
        last_map = self._events.setdefault(rec_id, {})
        last_ts = last_map.get(event_type)
        if last_ts and (now - last_ts) < self._debounce_seconds:
            return True
        last_map[event_type] = now
        return False

class AuditLogger:
    """سجل التدقيق"""
    @staticmethod
    def log_trigger_event(rec_id: int, event_type: str, symbol: str, trigger_price: float, actual_low: float, actual_high: float, decision: str = "EXECUTED"):
        audit_log.info("🎯 TRIGGER: rec_id=%d, type=%s, symbol=%s, trigger=%.6f, low=%.6f, high=%.6f, decision=%s", 
                      rec_id, event_type, symbol, trigger_price, actual_low, actual_high, decision)

class AlertService:
    """خدمة التنبيه مع الإصلاح الشامل للفشل الصامت"""
    
    def __init__(self, trade_service: TradeService, repo: RecommendationRepository, notifier: Any, admin_chat_id: Optional[str], main_loop: asyncio.AbstractEventLoop, streamer: Optional[PriceStreamer] = None):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)
        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()
        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._health_monitor_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self.debounce_manager = SmartDebounceManager(debounce_seconds=1.0)
        
        # ✅ الإصلاح: استخدام loop آمن
        try:
            safe_loop = main_loop if main_loop and main_loop.is_running() else asyncio.get_event_loop()
        except RuntimeError:
            safe_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(safe_loop)
            
        self.health_monitor = ServiceHealthMonitor(notifier, admin_chat_id, safe_loop, stale_threshold_sec=60)
        self.audit_logger = AuditLogger()
        self._tp_re = re.compile(r"^TP(\d+)$", flags=re.IGNORECASE)
        self._is_running = False
        self._restart_attempts = 0
        self._max_restart_attempts = 3

    # ✅ الإصلاح: دالة للتحقق من حالة الخدمة
    def is_processing(self) -> bool:
        """التحقق إذا كانت الخدمة تعالج البيانات"""
        return (self._is_running and 
                self._bg_thread is not None and 
                self._bg_thread.is_alive() and
                self._bg_loop is not None and 
                self._bg_loop.is_running())

    def get_queue_stats(self) -> Dict[str, Any]:
        """الحصول على إحصائيات الطابور والحالة"""
        return {
            "is_running": self._is_running,
            "background_thread_alive": self._bg_thread.is_alive() if self._bg_thread else False,
            "loop_running": self._bg_loop.is_running() if self._bg_loop else False,
            "queue_size": self.price_queue.qsize(),
            "total_processed": self.health_monitor.total_processed,
            "last_processed_seconds_ago": time.time() - self.health_monitor.last_processed_time,
            "consecutive_errors": self.health_monitor.consecutive_errors,
            "restart_attempts": self._restart_attempts
        }

    def _validate_trigger_data(self, trigger: Dict[str, Any]) -> bool:
        """التحقق من صحة بيانات التنبيه"""
        required_fields = ['rec_id', 'type', 'price', 'side']
        for field in required_fields:
            if field not in trigger or trigger[field] is None:
                log.error("❌ Validation failed: Missing field '%s' in trigger: %s", field, trigger)
                return False
        try:
            price = float(trigger['price'])
            if not (price > 0):
                raise ValueError("Price must be positive")
        except (ValueError, TypeError):
            log.error("❌ Validation failed: Invalid price in trigger: %s", trigger)
            return False
        return True

    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        """إضافة عنصر إلى قاموس المحفزات"""
        asset = (item.get("asset") or "").strip().upper()
        if not asset: 
            return
            
        status = item.get("status")
        side = item.get("side")
        rec_id = item.get("id")
        user_id = item.get("user_id")
        status_norm = status.name if hasattr(status, "name") else str(status).upper()
        
        def add_trigger(ttype, price, order_type=None):
            trigger = {"rec_id": rec_id, "user_id": user_id, "side": side, "type": ttype, "price": price, "order_type": order_type}
            if self._validate_trigger_data(trigger):
                trigger_dict.setdefault(asset, []).append(trigger)
                
        if status_norm in ("0", "PENDING"):
            add_trigger("ENTRY", item.get("entry"), item.get("order_type"))
        elif status_norm in ("1", "ACTIVE"):
            add_trigger("SL", item.get("stop_loss"))
            if item.get("profit_stop_price") is not None:
                add_trigger("PROFIT_STOP", item.get("profit_stop_price"))
            for idx, target in enumerate(item.get("targets") or []):
                add_trigger(f"TP{idx+1}", target.get("price"))

    async def build_triggers_index(self):
        """بناء فهرس المحفزات"""
        log.info("🔄 Building enhanced trigger index...")
        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        trigger_data = []
        try:
            with session_scope() as session:
                trigger_data = self.repo.list_all_active_triggers_data(session)
        except Exception as e:
            log.error("❌ Failed reading triggers: %s", e)
            return
            
        processed_count = 0
        for item in trigger_data:
            try:
                self._add_item_to_trigger_dict(new_triggers, item)
                processed_count += 1
            except Exception as e:
                log.error("❌ Failed processing trigger item: %s - Error: %s", item, e)
                
        async with self._triggers_lock:
            old_count = len(self.active_triggers)
            self.active_triggers = new_triggers
            
        log.info("✅ Trigger index: %d→%d symbols, %d recommendations", 
                old_count, len(new_triggers), processed_count)

    async def update_triggers_for_recommendation(self, rec_id: int):
        """تحديث محفزات توصية محددة"""
        log.debug("🔄 Updating triggers for Rec #%s", rec_id)
        item = None
        try:
            with session_scope() as session:
                item = self.repo.get_active_trigger_data_by_id(session, rec_id)
        except Exception as e:
            log.error("❌ Failed fetching trigger data for rec %s: %s", rec_id, e)
            return
            
        async with self._triggers_lock:
            # إزالة المحفزات القديمة
            for symbol in list(self.active_triggers.keys()):
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if not self.active_triggers[symbol]:
                    del self.active_triggers[symbol]
                    
            # إضافة المحفزات الجديدة
            if item:
                self._add_item_to_trigger_dict(self.active_triggers, item)
                log.info("✅ Updated triggers for Rec #%s", rec_id)
            else:
                log.info("🗑️ Removed triggers for Rec #%s", rec_id)

    async def remove_triggers_for_recommendation(self, rec_id: int):
        """إزالة محفزات توصية"""
        await self.update_triggers_for_recommendation(rec_id)

    async def _process_event_with_retry(self, event_type: str, rec_id: int, user_id: str, price: float, max_retries: int = 3) -> bool:
        """معالجة حدث مع إعادة المحاولة"""
        for attempt in range(max_retries):
            try:
                log.info("🔄 Processing %s event for rec %s (attempt %d/%d)", event_type, rec_id, attempt+1, max_retries)
                
                if event_type == "ENTRY":
                    await self.trade_service.process_activation_event(rec_id)
                elif self._tp_re.match(event_type):
                    idx = int(self._tp_re.match(event_type).group(1))
                    await self.trade_service.process_tp_hit_event(rec_id, user_id, idx, price)
                elif event_type == "SL":
                    await self.trade_service.process_sl_hit_event(rec_id, user_id, price)
                elif event_type == "PROFIT_STOP":
                    await self.trade_service.process_profit_stop_hit_event(rec_id, user_id, price)
                else:
                    log.error("❌ Unhandled event type: %s", event_type)
                    return False
                    
                log.info("✅ Successfully processed %s event for rec %s", event_type, rec_id)
                return True
                
            except Exception as e:
                log.error("❌ Attempt %d/%d failed for %s event on rec %s: %s", 
                         attempt + 1, max_retries, event_type, rec_id, e)
                if attempt == max_retries - 1:
                    log.critical("💥 Final attempt failed for event %s on rec #%d", event_type, rec_id)
                else:
                    await asyncio.sleep(2 ** attempt)
        return False

    def _is_price_condition_met(self, side: str, low_price: float, high_price: float, target_price: float, condition_type: str, order_type: Optional[Any] = None) -> bool:
        """التحقق من تحقيق شرط السعر"""
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()
        margin = target_price * 0.0001  # هامش 0.01%
        
        if side_upper == "LONG":
            if cond.startswith("TP"): return high_price >= target_price - margin
            if cond in ("SL", "PROFIT_STOP"): return low_price <= target_price + margin
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type else ""
                if "LIMIT" in ot: return low_price <= target_price + margin
                if "STOP" in ot: return high_price >= target_price - margin
                return low_price <= target_price + margin
        elif side_upper == "SHORT":
            if cond.startswith("TP"): return low_price <= target_price + margin
            if cond in ("SL", "PROFIT_STOP"): return high_price >= target_price - margin
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type else ""
                if "LIMIT" in ot: return high_price >= target_price - margin
                if "STOP" in ot: return low_price <= target_price + margin
                return high_price >= target_price - margin
        return False

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        """التحقق من التنبيهات ومعالجتها - الإصلاح الحرج"""
        symbol_upper = (symbol or "").upper()
        
        try:
            # ✅ سجل وصول البيانات
            log.debug("📥 Price update: %s L:%.6f H:%.6f", symbol_upper, low_price, high_price)
            
            async with self._triggers_lock:
                triggers_for_symbol = copy.deepcopy(self.active_triggers.get(symbol_upper, []))
                
            if not triggers_for_symbol:
                log.debug("🔍 No triggers for symbol: %s", symbol_upper)
                return
                
            log.debug("🔍 Checking %d triggers for %s", len(triggers_for_symbol), symbol_upper)
            
            processed_count = 0
            for trigger in triggers_for_symbol:
                rec_id = trigger.get("rec_id")
                ttype = trigger.get("type")
                
                if self.debounce_manager.is_debounced(rec_id, ttype):
                    continue
                    
                if self._is_price_condition_met(trigger.get("side"), low_price, high_price, trigger.get("price"), ttype, trigger.get("order_type")):
                    log.info("🎯 Trigger hit: %s %s @ %.6f (Range: %.6f-%.6f)", 
                            symbol_upper, ttype, trigger.get("price"), low_price, high_price)
                            
                    success = await self._process_event_with_retry(ttype, rec_id, trigger.get("user_id"), trigger.get("price"))
                    self.audit_logger.log_trigger_event(rec_id, ttype, symbol_upper, trigger.get("price"), low_price, high_price, "SUCCESS" if success else "FAILED")
                    processed_count += 1
                    
            if processed_count > 0:
                log.info("✅ Processed %d alerts for %s", processed_count, symbol_upper)
                
        except Exception as e:
            log.error("💥 Error in check_and_process_alerts for %s: %s", symbol_upper, e)
            self.health_monitor.record_error()

    # ✅ الإصلاح الحرج: إعادة كتابة حلقة المعالجة بشكل كامل
    async def _process_queue(self):
        """حلقة معالجة الطابور - الإصلاح الشامل للفشل الصامت"""
        log.info("🚀 Starting QUEUE PROCESSOR with silent failure fixes")
        await self.debounce_manager.start_cleanup_task()
        
        processing_errors = 0
        max_processing_errors = 5
        empty_cycles = 0
        max_empty_cycles = 10
        
        while self._is_running:
            try:
                # ✅ استخدم timeout قصير للكشف السريع عن المشاكل
                try:
                    item = await asyncio.wait_for(self.price_queue.get(), timeout=5.0)
                    symbol, low_price, high_price, timestamp = item
                    empty_cycles = 0  # إعادة تعيين عداد الدورات الفارغة
                except asyncio.TimeoutError:
                    empty_cycles += 1
                    if empty_cycles >= max_empty_cycles:
                        log.warning("⚠️ Queue empty for %d cycles - checking health", empty_cycles)
                        empty_cycles = 0
                    continue
                
                # ✅ سجل استلام البيانات
                log.debug("📥 Processing: %s L:%.6f H:%.6f", symbol, low_price, high_price)
                
                try:
                    # ✅ معالجة البيانات مع عزل الأخطاء
                    await self.check_and_process_alerts(symbol, low_price, high_price)
                    self.health_monitor.record_processing()
                    processing_errors = 0  # إعادة تعيين عداد الأخطاء
                    
                except Exception as processing_error:
                    processing_errors += 1
                    log.error("💥 Processing error #%d: %s", processing_errors, processing_error)
                    
                    if processing_errors >= max_processing_errors:
                        log.critical("💥 Max processing errors reached -可能需要 إعادة التشغيل")
                        break
                        
            except asyncio.CancelledError:
                log.info("🛑 Queue processor cancelled")
                break
                
            except Exception as outer_error:
                log.error("💥 Outer loop error in queue processor: %s", outer_error)
                processing_errors += 1
                
                if processing_errors >= max_processing_errors:
                    log.critical("💥 Critical failure in queue processor - restart needed")
                    break
                    
            finally:
                # ✅ تأكد من وضع علامة task_done حتى لو حدث خطأ
                try:
                    self.price_queue.task_done()
                except Exception:
                    pass

        log.warning("🛑 Queue processor stopped -可能需要 إعادة التشغيل")

    async def _run_health_monitor(self, interval_seconds: int = 30):
        """تشغيل مراقب الصحة"""
        log.info("❤️ Health monitor started (interval=%ss)", interval_seconds)
        
        while self._is_running:
            await asyncio.sleep(interval_seconds)
            self.health_monitor.check_health()
            
            # ✅ سجل حجم الطابور بشكل دوري
            queue_size = self.price_queue.qsize()
            if queue_size != self.health_monitor.last_queue_size:
                stats = self.get_queue_stats()
                log.info("📊 Queue stats: size=%d, processed=%d, errors=%d, running=%s", 
                        queue_size, stats["total_processed"], stats["consecutive_errors"], stats["is_running"])
                self.health_monitor.last_queue_size = queue_size

    async def _run_index_sync(self, interval_seconds: int = 300):
        """مهمة مزامنة الفهرس"""
        log.info("🔄 Index sync started (interval=%ss)", interval_seconds)
        while self._is_running:
            await asyncio.sleep(interval_seconds)
            try:
                await self.build_triggers_index()
            except Exception as e:
                log.error("❌ Index sync failed: %s", e)

    # ✅ الإصلاح: إضافة آلية الاستعادة الذاتية
    async def _supervisor_loop(self):
        """حلقة المشرف لمراقبة وإعادة تشغيل المهام المعطلة"""
        log.info("👨‍💼 Supervisor loop started")
        
        while self._is_running:
            await asyncio.sleep(60)  # فحص كل دقيقة
            
            # ✅ التحقق من حالة المهام
            tasks_ok = True
            
            # التحقق من مهمة المعالجة
            if self._processing_task is None or self._processing_task.done():
                log.warning("⚠️ Processing task is not running - restarting")
                self._processing_task = asyncio.create_task(self._process_queue())
                tasks_ok = False
                
            # التحقق من مهمة المزامنة
            if self._index_sync_task is None or self._index_sync_task.done():
                log.warning("⚠️ Index sync task is not running - restarting")
                self._index_sync_task = asyncio.create_task(self._run_index_sync())
                tasks_ok = False
                
            # التحقق من مهمة المراقبة
            if self._health_monitor_task is None or self._health_monitor_task.done():
                log.warning("⚠️ Health monitor task is not running - restarting")
                self._health_monitor_task = asyncio.create_task(self._run_health_monitor())
                tasks_ok = False
                
            if not tasks_ok:
                log.info("🔄 Supervisor restarted one or more tasks")

    def start(self):
        """بدء الخدمة مع آلية استعادة"""
        if self._bg_thread and self._bg_thread.is_alive():
            log.warning("⚠️ AlertService background thread already running.")
            return
            
        self._is_running = True
        self._restart_attempts = 0
            
        def _bg_runner():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._bg_loop = loop
                
                async def startup():
                    # ✅ ابدأ الـ streamer أولاً
                    self.streamer.start()
                    
                    # ✅ انتظر قليلاً قبل بدء المهام الأخرى
                    await asyncio.sleep(2)
                    
                    # ✅ ابدأ المهام الأساسية مع آلية استعادة
                    self._processing_task = asyncio.create_task(self._process_queue())
                    self._index_sync_task = asyncio.create_task(self._run_index_sync())
                    self._health_monitor_task = asyncio.create_task(self._run_health_monitor())
                    self._supervisor_task = asyncio.create_task(self._supervisor_loop())
                    
                    log.info("✅ All AlertService tasks started with supervisor")
                    
                loop.run_until_complete(startup())
                log.info("🔄 AlertService background loop starting...")
                loop.run_forever()
                
            except Exception as e:
                log.critical("💥 AlertService background runner crashed: %s", e, exc_info=True)
                self._restart_attempts += 1
                
            finally:
                self._is_running = False
                if self._bg_loop and self._bg_loop.is_running():
                    # إيقاف جميع المهام بشكل أنيق
                    tasks = asyncio.all_tasks(loop=self._bg_loop)
                    for task in tasks: 
                        task.cancel()
                    async def gather_cancelled(): 
                        await asyncio.gather(*tasks, return_exceptions=True)
                    self._bg_loop.run_until_complete(gather_cancelled())
                    self._bg_loop.close()
                log.info("🛑 AlertService background loop stopped.")
                
                # ✅ محاولة إعادة التشغيل التلقائي
                if self._restart_attempts < self._max_restart_attempts:
                    log.warning("🔄 Attempting auto-restart (%d/%d)", self._restart_attempts + 1, self._max_restart_attempts)
                    time.sleep(5)
                    self.start()

        self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
        self._bg_thread.start()
        log.info("✅ AlertService v19.1.0 started in background thread with auto-recovery")

    def stop(self):
        """إيقاف الخدمة"""
        self._is_running = False
        
        if self._bg_loop and self._bg_loop.is_running():
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            
        if self._bg_thread:
            self._bg_thread.join(timeout=10.0)
            
        self.streamer.stop()
        self._bg_thread = None
        self._bg_loop = None
        log.info("🛑 AlertService v19.1.0 stopped.")

    # ✅ دالة مساعدة لإعادة التشغيل الآمن
    def restart(self):
        """إعادة تشغيل الخدمة بشكل آمن"""
        log.info("🔄 Restarting AlertService...")
        self.stop()
        time.sleep(2)
        self.start()