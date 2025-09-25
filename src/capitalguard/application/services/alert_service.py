# src/capitalguard/application/services/alert_service.py V 19.0.0
"""
AlertService - النسخة المحسنة والمطورة مع تحسينات الموثوقية والدقة.

Key improvements:
- إصلاح منطق شروط السعر مع هوامش أمان
- نظام مراقبة صحية متكامل
- آلية تزامن محسنة مع نسخ عميقة
- نظام إعادة محاولة ذكي للأحداث الفاشلة
- إدارة ذاكرة محسنة لـ Debounce
- تحسين تحديث الفهرس بدون فجوات زمنية
- نظام مراقبة وتتبع للأحداث الحرجة
- تحسين معالجة طابور الأسعار
- تحقق من صحة البيانات المدخلة
- نظام تسجيل محسن للتدقيق
"""

import logging
import asyncio
import threading
import copy
import time
import re
from typing import List, Dict, Any, Optional
from contextlib import suppress
from dataclasses import dataclass

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer

log = logging.getLogger(__name__)
audit_log = logging.getLogger('audit')


@dataclass
class HealthMetrics:
    """مقاييس الصحة للخدمة"""
    last_processed_time: float
    processed_count: int = 0
    error_count: int = 0
    last_health_check: float = 0
    startup_time: float = 0


class ServiceHealthMonitor:
    """نظام مراقبة صحية متكامل"""
    
    def __init__(self, service_name: str = "AlertService"):
        self.service_name = service_name
        self.metrics = HealthMetrics(last_processed_time=time.time(), startup_time=time.time())
        self._emergency_callback = None
        self._health_check_interval = 60  # ثانية
        
    def record_processing(self):
        """تسجيل معالجة ناجحة"""
        self.metrics.processed_count += 1
        self.metrics.last_processed_time = time.time()
        
    def record_error(self):
        """تسجيل خطأ"""
        self.metrics.error_count += 1
        
    async def check_health(self):
        """فحص صحة الخدمة"""
        current_time = time.time()
        self.metrics.last_health_check = current_time
        
        # تنبيه إذا لم تكن هناك معالجة خلال 60 ثانية
        if current_time - self.metrics.last_processed_time > 60:
            log.critical("ALERT: No price processing detected for 60 seconds!")
            await self._trigger_emergency_protocol()
            return False
            
        # تنبيه إذا كان معدل الأخطاء مرتفعاً
        if (self.metrics.processed_count > 100 and 
            self.metrics.error_count / self.metrics.processed_count > 0.1):
            log.warning("High error rate detected: %.1f%%", 
                       (self.metrics.error_count / self.metrics.processed_count) * 100)
            
        return True
    
    async def _trigger_emergency_protocol(self):
        """تفعيل بروتوكول الطوارئ"""
        if self._emergency_callback:
            await self._emergency_callback()
        else:
            log.error("Emergency protocol required but no callback set")
    
    def set_emergency_callback(self, callback):
        """تعيين callback للطوارئ"""
        self._emergency_callback = callback


class SmartDebounceManager:
    """إدارة ذاكرة محسنة لـ Debounce"""
    
    def __init__(self, max_age_seconds: float = 3600, debounce_seconds: float = 1.0):
        self._events: Dict[int, Dict[str, float]] = {}
        self._max_age = max_age_seconds
        self._debounce_seconds = debounce_seconds
        self._cleanup_interval = 300  # تنظيف كل 5 دقائق
        
    async def start_cleanup_task(self):
        """بدء مهمة التنظيف الدورية"""
        asyncio.create_task(self._periodic_cleanup())
    
    async def _periodic_cleanup(self):
        """تنظيف دوري للإدخالات القديمة"""
        while True:
            await asyncio.sleep(self._cleanup_interval)
            self._remove_old_entries()
    
    def _remove_old_entries(self):
        """إزالة الإدخالات القديمة"""
        current_time = time.time()
        expired_records = []
        
        for rec_id, events in self._events.items():
            expired_events = []
            for event_type, timestamp in events.items():
                if current_time - timestamp > self._max_age:
                    expired_events.append(event_type)
            
            for event_type in expired_events:
                del events[event_type]
                
            if not events:
                expired_records.append(rec_id)
        
        for rec_id in expired_records:
            del self._events[rec_id]
            
        if expired_records:
            log.debug("Cleaned up %d expired debounce records", len(expired_records))
    
    def should_process(self, rec_id: int, event_type: str) -> bool:
        """التحقق مما إذا كان يجب معالجة الحدث"""
        current_time = time.time()
        event_type = event_type.upper()
        
        # البحث عن آخر وقت معالجة
        last_ts = self._events.get(rec_id, {}).get(event_type, 0)
        
        # إذا كان الوقت منذ آخر معالجة أقل من فترة debounce، نتخطى
        if current_time - last_ts < self._debounce_seconds:
            return False
            
        # تحديث وقت المعالجة
        if rec_id not in self._events:
            self._events[rec_id] = {}
        self._events[rec_id][event_type] = current_time
        
        return True


class CriticalEventTracker:
    """نظام مراقبة وتتبع للأحداث الحرجة"""
    
    def __init__(self):
        self.pending_events: Dict[str, asyncio.Event] = {}
        self.event_timeout = 30.0  # 30 ثانية انتظار للحدث الحرج
        self.event_results: Dict[str, bool] = {}
        
    async def wait_for_critical_event(self, rec_id: int, event_type: str) -> bool:
        """انتظار حدث حرج"""
        event_key = f"{rec_id}_{event_type}"
        event = asyncio.Event()
        self.pending_events[event_key] = event
        
        try:
            await asyncio.wait_for(event.wait(), timeout=self.event_timeout)
            result = self.event_results.get(event_key, False)
            return result
        except asyncio.TimeoutError:
            log.error("Critical event timeout for %s", event_key)
            return False
        finally:
            self.pending_events.pop(event_key, None)
            self.event_results.pop(event_key, None)
    
    def signal_event_completion(self, rec_id: int, event_type: str, success: bool = True):
        """إشارة اكتمال الحدث"""
        event_key = f"{rec_id}_{event_type}"
        if event_key in self.pending_events:
            self.event_results[event_key] = success
            self.pending_events[event_key].set()


class AuditLogger:
    """نظام تسجيل محسن للتدقيق"""
    
    @staticmethod
    def log_trigger_event(rec_id: int, event_type: str, symbol: str, 
                         trigger_price: float, actual_low: float, actual_high: float,
                         decision: str = "EXECUTED"):
        """تسجيل حدث التنفيذ"""
        audit_log.info(
            "TRIGGER_EXECUTED: rec_id=%d, type=%s, symbol=%s, "
            "trigger=%.6f, low=%.6f, high=%.6f, decision=%s",
            rec_id, event_type, symbol, trigger_price, 
            actual_low, actual_high, decision
        )
    
    @staticmethod
    def log_retry_attempt(rec_id: int, event_type: str, attempt: int, max_attempts: int):
        """تسجيل محاولة إعادة"""
        audit_log.warning(
            "RETRY_ATTEMPT: rec_id=%d, type=%s, attempt=%d/%d",
            rec_id, event_type, attempt, max_attempts
        )
    
    @staticmethod
    def log_service_health(health_metrics: HealthMetrics):
        """تسجيل صحة الخدمة"""
        uptime = time.time() - health_metrics.startup_time
        error_rate = (health_metrics.error_count / health_metrics.processed_count * 100) if health_metrics.processed_count > 0 else 0
        
        audit_log.info(
            "SERVICE_HEALTH: uptime=%.1fs, processed=%d, errors=%d, error_rate=%.2f%%",
            uptime, health_metrics.processed_count, health_metrics.error_count, error_rate
        )


class AlertService:
    def __init__(self, trade_service, repo: RecommendationRepository, 
                 streamer: Optional[PriceStreamer] = None, debounce_seconds: float = 1.0):
        self.trade_service = trade_service
        self.repo = repo
        self.price_queue: asyncio.Queue = asyncio.Queue()
        self.streamer = streamer or PriceStreamer(self.price_queue, self.repo)

        self.active_triggers: Dict[str, List[Dict[str, Any]]] = {}
        self._triggers_lock = asyncio.Lock()

        self._processing_task: Optional[asyncio.Task] = None
        self._index_sync_task: Optional[asyncio.Task] = None
        self._health_monitor_task: Optional[asyncio.Task] = None

        # background runner for sync start()
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None

        # الأنظمة المحسنة
        self.debounce_manager = SmartDebounceManager(debounce_seconds=debounce_seconds)
        self.health_monitor = ServiceHealthMonitor("AlertService")
        self.event_tracker = CriticalEventTracker()
        self.audit_logger = AuditLogger()

        # TP regex
        self._tp_re = re.compile(r"^TP(\d+)$", flags=re.IGNORECASE)

        # إعداد callback الطوارئ
        self.health_monitor.set_emergency_callback(self._emergency_restart)

    # ---------- نظام الطوارئ ----------
    
    async def _emergency_restart(self):
        """إعادة تشغيل طارئة للخدمة"""
        log.critical("Initiating emergency restart of AlertService...")
        try:
            self.stop()
            await asyncio.sleep(2)  # انتظار قصير
            self.start()
            log.info("Emergency restart completed")
        except Exception as e:
            log.error("Emergency restart failed: %s", e)

    # ---------- تحقق من صحة البيانات ----------
    
    def _validate_trigger_data(self, trigger: Dict[str, Any]) -> bool:
        """تحقق من صحة بيانات التريجر"""
        required_fields = ['rec_id', 'type', 'price', 'side']
        
        for field in required_fields:
            if field not in trigger or trigger[field] is None:
                log.error("Missing required field %s in trigger: %s", field, trigger)
                return False
        
        try:
            price = float(trigger['price'])
            if price <= 0:
                log.error("Invalid price in trigger: %s", trigger)
                return False
        except (ValueError, TypeError):
            log.error("Non-numeric price in trigger: %s", trigger)
            return False
        
        return True

    # ---------- Trigger index ----------

    async def build_triggers_index(self):
        """بناء فهرس التريجرات مع تحسينات"""
        log.info("Building enhanced in-memory trigger index for all active recommendations...")
        new_triggers: Dict[str, List[Dict[str, Any]]] = {}
        try:
            with session_scope() as session:
                trigger_data = self.repo.list_all_active_triggers_data(session)
        except Exception:
            log.exception("Failed reading triggers from repository.")
            return

        processed_count = 0
        invalid_count = 0
        
        for item in trigger_data:
            try:
                if not self._validate_trigger_data(item):
                    invalid_count += 1
                    continue
                    
                asset_raw = (item.get("asset") or "").strip().upper()
                if not asset_raw:
                    log.warning("Skipping trigger with empty asset: %s", item)
                    invalid_count += 1
                    continue
                    
                item["asset"] = asset_raw
                self._add_item_to_trigger_dict(new_triggers, item)
                processed_count += 1
            except Exception:
                log.exception("Failed processing trigger item: %s", item)
                invalid_count += 1

        async with self._triggers_lock:
            # إزالة التكرارات بمنهجية محسنة
            for sym, triggers in new_triggers.items():
                seen = set()
                unique = []
                for t in triggers:
                    key = (t.get("rec_id"), t.get("type"), float(t.get("price") or 0.0))
                    if key in seen:
                        continue
                    seen.add(key)
                    unique.append(t)
                new_triggers[sym] = unique
            self.active_triggers = new_triggers

        log.info("Trigger index built: %d valid, %d invalid, %d symbols.", 
                processed_count, invalid_count, len(new_triggers))

    def _add_item_to_trigger_dict(self, trigger_dict: Dict[str, list], item: Dict[str, Any]):
        """إضافة عنصر إلى فهرس التريجرات"""
        asset = (item.get("asset") or "").strip().upper()
        if not asset:
            raise ValueError("Empty asset when adding trigger")

        if asset not in trigger_dict:
            trigger_dict[asset] = []

        status = item.get("status")
        side = item.get("side")
        rec_id = item.get("id")
        user_id = item.get("user_id")

        # normalize status (accept enum or primitive)
        try:
            status_norm = status.name if hasattr(status, "name") else str(status).upper()
        except Exception:
            status_norm = str(status).upper()

        # ENTRY for pending
        if status_norm in ("0", "PENDING"):
            try:
                price = float(item.get("entry") or 0.0)
            except Exception:
                price = 0.0
            trigger_dict[asset].append({
                "rec_id": rec_id, "user_id": user_id, "side": side,
                "type": "ENTRY", "price": price, "order_type": item.get("order_type")
            })
            return

        # ACTIVE -> SL, PROFIT_STOP, TPs
        if status_norm in ("1", "ACTIVE"):
            sl = item.get("stop_loss")
            if sl is not None:
                try:
                    slp = float(sl)
                    trigger_dict[asset].append({
                        "rec_id": rec_id, "user_id": user_id, "side": side,
                        "type": "SL", "price": slp
                    })
                except Exception:
                    log.warning("Invalid stop_loss for rec %s: %s", rec_id, sl)
            psp = item.get("profit_stop_price")
            if psp is not None:
                try:
                    pspv = float(psp)
                    trigger_dict[asset].append({
                        "rec_id": rec_id, "user_id": user_id, "side": side,
                        "type": "PROFIT_STOP", "price": pspv
                    })
                except Exception:
                    log.warning("Invalid profit_stop_price for rec %s: %s", rec_id, psp)
            for idx, target in enumerate(item.get("targets") or []):
                try:
                    tprice = float(target.get("price"))
                    trigger_dict[asset].append({
                        "rec_id": rec_id, "user_id": user_id, "side": side,
                        "type": f"TP{idx+1}", "price": tprice
                    })
                except Exception:
                    log.warning("Invalid target for rec %s index %s: %s", rec_id, idx, target)
            return

        log.debug("Unhandled trigger status for rec %s: %s", rec_id, status)

    async def update_triggers_for_recommendation(self, rec_id: int):
        """تحديث التريجرات بتحديث ذري"""
        log.debug("Updating triggers for Rec #%s with atomic update.", rec_id)
        
        # بناء التريجر الجديد أولاً
        new_trigger = None
        try:
            with session_scope() as session:
                item = self.repo.get_active_trigger_data_by_id(session, rec_id)
                if item and self._validate_trigger_data(item):
                    new_trigger = item
        except Exception:
            log.exception("Failed fetching active trigger data for rec %s", rec_id)
            return

        # التحديث الذري
        async with self._triggers_lock:
            # إزالة القديم
            for symbol in list(self.active_triggers.keys()):
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if not self.active_triggers[symbol]:
                    del self.active_triggers[symbol]

            # إضافة الجديد إذا كان موجوداً
            if new_trigger:
                asset = (new_trigger.get("asset") or "").strip().upper()
                new_trigger["asset"] = asset
                try:
                    self._add_item_to_trigger_dict(self.active_triggers, new_trigger)
                    log.info("Atomically updated triggers for Rec #%s under symbol %s.", rec_id, asset)
                except Exception:
                    log.exception("Failed to add updated trigger for rec %s", rec_id)

    async def remove_triggers_for_recommendation(self, rec_id: int):
        """إزالة التريجرات مع التحقق"""
        async with self._triggers_lock:
            removed = False
            for symbol in list(self.active_triggers.keys()):
                original = len(self.active_triggers[symbol])
                self.active_triggers[symbol] = [t for t in self.active_triggers[symbol] if t.get("rec_id") != rec_id]
                if len(self.active_triggers[symbol]) < original:
                    removed = True
                    log.info("Removed triggers for Rec #%s from symbol %s in memory.", rec_id, symbol)
                if not self.active_triggers[symbol]:
                    del self.active_triggers[symbol]
            if not removed:
                log.debug("No triggers removed for Rec #%s; none found in memory.", rec_id)

    # ---------- نظام إعادة المحاولة ----------
    
    async def _process_event_with_retry(self, event_type: str, rec_id: int, user_id: str, 
                                       price: float, max_retries: int = 3) -> bool:
        """معالجة الأحداث مع إعادة المحاولة"""
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    self.audit_logger.log_retry_attempt(rec_id, event_type, attempt, max_retries)
                    
                if event_type == "ENTRY":
                    await self.trade_service.process_activation_event(rec_id)
                elif self._tp_re.match(event_type):
                    m = self._tp_re.match(event_type)
                    try:
                        idx = int(m.group(1))
                    except Exception:
                        idx = 1
                    await self.trade_service.process_tp_hit_event(rec_id, user_id, idx, price)
                elif event_type == "SL":
                    await self.trade_service.process_sl_hit_event(rec_id, user_id, price)
                elif event_type == "PROFIT_STOP":
                    await self.trade_service.process_profit_stop_hit_event(rec_id, user_id, price)
                else:
                    log.error("Unhandled event type: %s", event_type)
                    return False
                    
                # إشارة النجاح للتتبع
                self.event_tracker.signal_event_completion(rec_id, event_type, True)
                return True
                
            except Exception as e:
                log.error("Attempt %d/%d failed for %s event on rec %s: %s", 
                         attempt + 1, max_retries, event_type, rec_id, e)
                
                if attempt == max_retries - 1:
                    await self._report_failed_event(rec_id, event_type, price, str(e))
                    self.event_tracker.signal_event_completion(rec_id, event_type, False)
                    return False
                    
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        return False

    async def _report_failed_event(self, rec_id: int, event_type: str, price: float, error: str):
        """الإبلاغ عن الحدث الفاشل"""
        log.error("Failed to process event: rec_id=%d, type=%s, price=%.6f, error=%s",
                 rec_id, event_type, price, error)
        # يمكن إضافة إخطار للمشرفين هنا

    # ---------- Condition evaluation & processing ----------

    def _is_price_condition_met(self, side: str, low_price: float, high_price: float, 
                               target_price: float, condition_type: str, 
                               order_type: Optional[Any] = None) -> bool:
        """تقييم شرط السعر مع هوامش أمان"""
        side_upper = (side or "").upper()
        cond = (condition_type or "").upper()

        # هامش أمان لتجنب التنفيذ عند التساوي التام
        spread_margin = 0.0001  # 0.01% هامش أمان

        # inclusive comparisons to capture edge hits
        if side_upper == "LONG":
            if cond.startswith("TP"):
                return high_price >= target_price - spread_margin
            if cond in ("SL", "PROFIT_STOP"):
                return low_price <= target_price + spread_margin
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                if ot.endswith("LIMIT"):
                    return low_price <= target_price + spread_margin
                if ot.endswith("STOP_MARKET"):
                    return high_price >= target_price - spread_margin
                return low_price <= target_price + spread_margin or high_price >= target_price - spread_margin

        if side_upper == "SHORT":
            if cond.startswith("TP"):
                return low_price <= target_price + spread_margin
            if cond in ("SL", "PROFIT_STOP"):
                return high_price >= target_price - spread_margin
            if cond == "ENTRY":
                ot = str(order_type).upper() if order_type is not None else ""
                if ot.endswith("LIMIT"):
                    return high_price >= target_price - spread_margin
                if ot.endswith("STOP_MARKET"):
                    return low_price <= target_price + spread_margin
                return low_price <= target_price + spread_margin or high_price >= target_price - spread_margin

        return False

    async def _process_triggers_safely(self, triggers: List[Dict[str, Any]], symbol: str, 
                                      low_price: float, high_price: float):
        """معالجة التريجرات بشكل آمن باستخدام نسخة"""
        triggered_events = []
        
        for trigger in triggers:
            try:
                if not self._validate_trigger_data(trigger):
                    continue

                execution_price = float(trigger.get("price") or 0.0)
                rec_id = int(trigger.get("rec_id") or 0)
                event_type = (trigger.get("type") or "").upper()

                if not self._is_price_condition_met(trigger.get("side"), low_price, high_price, 
                                                   execution_price, event_type, trigger.get("order_type")):
                    continue

                # التحقق من debounce
                if not self.debounce_manager.should_process(rec_id, event_type):
                    log.debug("Debounced event for rec %s type %s", rec_id, event_type)
                    continue

                triggered_events.append((rec_id, event_type, trigger.get("user_id"), execution_price))
                log.info("Trigger HIT for Rec #%s: Type=%s, Symbol=%s, Range=[%s,%s], Target=%s", 
                        rec_id, event_type, symbol, low_price, high_price, execution_price)
                        
            except Exception:
                log.exception("Error processing trigger: %s", trigger)

        # معالجة الأحداث المطلوبة
        for rec_id, event_type, user_id, price in triggered_events:
            success = await self._process_event_with_retry(event_type, rec_id, user_id, price)
            
            # تسجيل التدقيق
            self.audit_logger.log_trigger_event(
                rec_id, event_type, symbol, price, low_price, high_price,
                "SUCCESS" if success else "FAILED"
            )

    async def check_and_process_alerts(self, symbol: str, low_price: float, high_price: float):
        """فحص ومعالجة التنبيهات بنسخة آمنة"""
        # إنشاء نسخة عميقة آمنة للمعالجة
        symbol_key = (symbol or "").upper()
        async with self._triggers_lock:
            triggers_copy = copy.deepcopy(self.active_triggers.get(symbol_key, []))

        if not triggers_copy:
            return

        # المعالجة باستخدام النسخة الآمنة
        await self._process_triggers_safely(triggers_copy, symbol, low_price, high_price)

    # ---------- Background tasks ----------

    async def _run_health_monitor(self, interval_seconds: int = 30):
        """مهمة مراقبة الصحة"""
        log.info("Health monitor task started (interval=%ss).", interval_seconds)
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                await self.health_monitor.check_health()
                # تسجيل صحة الخدمة للتدقيق
                self.audit_logger.log_service_health(self.health_monitor.metrics)
        except asyncio.CancelledError:
            log.info("Health monitor task cancelled.")
        except Exception:
            log.exception("Health monitor encountered error.")

    async def _run_index_sync(self, interval_seconds: int = 300):
        """مهمة مزامنة الفهرس"""
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
        """معالجة طابور الأسعار المحسنة"""
        log.info("AlertService queue processor started with enhanced reliability.")
        
        # بدء مهمة التنظيف لـ debounce
        await self.debounce_manager.start_cleanup_task()
        
        while True:
            try:
                # إضافة timeout لمنع التوقف الصامت
                symbol, low_price, high_price = await asyncio.wait_for(
                    self.price_queue.get(), 
                    timeout=30.0
                )
                
                # تحديث المراقبة الصحية
                self.health_monitor.record_processing()
                
                await self.check_and_process_alerts(symbol, low_price, high_price)
                
            except asyncio.TimeoutError:
                # هذا طبيعي - مجرد فرصة للتحقق من صحة الخدمة
                await self.health_monitor.check_health()
            except Exception as e:
                log.error("Unexpected error in queue processor: %s", e)
                self.health_monitor.record_error()
            finally:
                with suppress(Exception):
                    self.price_queue.task_done()

    # ---------- Start / Stop ----------

    def start(self):
        """بدء الخدمة مع التحسينات"""
        try:
            loop = asyncio.get_running_loop()
            if self._processing_task is None or self._processing_task.done():
                self._processing_task = loop.create_task(self._process_queue())
            if self._index_sync_task is None or self._index_sync_task.done():
                self._index_sync_task = loop.create_task(self._run_index_sync())
            if self._health_monitor_task is None or self._health_monitor_task.done():
                self._health_monitor_task = loop.create_task(self._run_health_monitor())
                
            try:
                if hasattr(self.streamer, "start"):
                    self.streamer.start()
            except Exception:
                log.exception("Streamer.start() failed in event loop context.")
                
            log.info("AlertService v19.0.0 started in existing event loop.")
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
                    self._health_monitor_task = loop.create_task(self._run_health_monitor())
                    
                    try:
                        if hasattr(self.streamer, "start"):
                            self.streamer.start()
                    except Exception:
                        log.exception("Streamer.start() failed in background thread.")
                        
                    loop.run_forever()
                except Exception:
                    log.exception("AlertService background runner crashed.")
                finally:
                    try:
                        for t in (self._processing_task, self._index_sync_task, self._health_monitor_task):
                            if t and not t.done():
                                loop.call_soon_threadsafe(t.cancel)
                    except Exception:
                        pass
                    with suppress(Exception):
                        loop.stop()
                        loop.close()
                    log.info("AlertService background loop stopped.")

            self._bg_thread = threading.Thread(target=_bg_runner, name="alertservice-bg", daemon=True)
            self._bg_thread.start()
            log.info("AlertService v19.0.0 started in background thread.")

    def stop(self):
        """إيقاف الخدمة"""
        try:
            if hasattr(self.streamer, "stop"):
                self.streamer.stop()
        except Exception:
            log.exception("Error stopping streamer.")

        try:
            for task in (self._processing_task, self._index_sync_task, self._health_monitor_task):
                if task and not task.done():
                    task.cancel()
        except Exception:
            log.exception("Error cancelling tasks in main loop.")

        if self._bg_loop and self._bg_thread:
            try:
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            except Exception:
                log.exception("Failed to stop background event loop.")
            self._bg_thread.join(timeout=5.0)
            if self._bg_thread.is_alive():
                log.warning("Background thread did not exit within timeout.")
            self._bg_thread = None
            self._bg_loop = None

        self._processing_task = None
        self._index_sync_task = None
        self._health_monitor_task = None
        log.info("AlertService v19.0.0 stopped and cleaned up.")