#START FILE src/capitalguard/application/services/alert_service.py #v25
import logging
import os
import asyncio
from typing import Optional

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else default

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try:
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return None


class AlertService:
    def __init__(self, price_service: PriceService, notifier: any, repo: RecommendationRepository, trade_service: TradeService):
        self.price_service = price_service
        self.notifier = notifier
        self.repo = repo
        self.trade_service = trade_service

    def schedule_job(self, app, interval_sec: int = 60):
        jq = getattr(app, "job_queue", None)
        if jq is None:
            log.warning("JobQueue not available; skipping alert scheduling.")
            return
        try:
            jq.run_repeating(self._job, interval=interval_sec, first=15)
            log.info("Alert job scheduled every %ss", interval_sec)
        except Exception as e:
            log.warning("Failed to schedule alert job: %s", e)

    async def _job(self, context):
        try:
            num_actions = await asyncio.to_thread(self.check_once)
            if num_actions and num_actions > 0:
                log.info("Alert job finished, triggered %d actions.", num_actions)
        except Exception as e:
            log.exception("Alert job exception: %s", e)

    # ============== Helpers for targets compatibility (number or object) ==============
    @staticmethod
    def _extract_tp_components(tp) -> tuple[float, float]:
        """
        Returns (price, close_percent). Supports either:
        - numeric targets (float/int) -> (price, 0.0)
        - object with attributes `.price` and optional `.close_percent`
        """
        try:
            price = float(getattr(tp, "price"))
        except Exception:
            price = float(tp)
        try:
            close_percent = float(getattr(tp, "close_percent", 0) or 0)
        except Exception:
            close_percent = 0.0
        return price, close_percent

    def check_once(self) -> int:
        action_count = 0
        active_recs = self.repo.list_open()  # نجلب فقط الصفقات التي ما زالت مفتوحة
        auto_close_enabled = _env_bool("AUTO_CLOSE_ENABLED", False)
        near_alert_pct = _env_float("NEAR_ALERT_PCT", 1.5) / 100.0

        for rec in active_recs:
            if rec.status != RecommendationStatus.ACTIVE:
                continue

            try:
                price = self.price_service.get_preview_price(rec.asset.value, rec.market)
                if price is None:
                    continue

                # Always update HH/LL tracking (سلوك قائم)
                self.trade_service.update_price_tracking(rec.id, price)
                side = rec.side.value.upper()

                # --- Priority Rule: SL -> Profit Stop -> Final TP (السلوك القديم محفوظ) ---

                # 1) Original Stop Loss (highest priority)
                sl = rec.stop_loss.value
                if (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl):
                    log.warning(f"Auto-closing rec #{rec.id} due to SL hit at price {price}.")
                    self.trade_service.close(rec.id, price, reason="SL_HIT")
                    action_count += 1
                    continue

                # 2) Manual Profit Stop (second priority)
                if rec.profit_stop_price is not None:
                    if (side == "LONG" and price <= rec.profit_stop_price) or \
                       (side == "SHORT" and price >= rec.profit_stop_price):
                        log.info(f"Auto-closing rec #{rec.id} due to Profit Stop hit at price {price}.")
                        self.trade_service.close(rec.id, price, reason="PROFIT_STOP_HIT")
                        action_count += 1
                        continue

                # 3) Final TP (lowest priority; only if strategy allows and auto-close enabled)
                if auto_close_enabled and rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP and rec.targets.values:
                    last_tp_raw = rec.targets.values[-1]
                    last_tp, _ = self._extract_tp_components(last_tp_raw)
                    if (side == "LONG" and price >= last_tp) or (side == "SHORT" and price <= last_tp):
                        log.info(f"Auto-closing rec #{rec.id} due to final TP hit at price {price}.")
                        self.trade_service.close(rec.id, price, reason="FINAL_TP_HIT")
                        action_count += 1
                        continue

                # ✅ --- Auto Partial Profit (جديد) ---
                # ينفّذ جني أرباح جزئي تلقائي لكل هدف لديه close_percent > 0
                # مرة واحدة فقط لكل هدف (بحدث مانع للتكرار)
                if rec.targets.values:
                    for i, tp_raw in enumerate(rec.targets.values):
                        tp_price, close_percent = self._extract_tp_components(tp_raw)
                        if close_percent <= 0:
                            continue

                        event_type = f"TP{i+1}_PARTIAL_AUTO"
                        if self.repo.check_if_event_exists(rec.id, event_type):
                            continue

                        is_tp_hit = (side == "LONG" and price >= tp_price) or (side == "SHORT" and price <= tp_price)
                        if is_tp_hit:
                            log.info(f"Auto partial profit triggered for rec #{rec.id} at TP{i+1} (pct={close_percent}%, price={tp_price}).")

                            # تنفيذ الإغلاق الجزئي عبر الخدمة (يفترض أنها تسجل الحدث الداخلي والإشعارات حسب منطقك)
                            self.trade_service.take_partial_profit(
                                rec.id,
                                close_percent,
                                tp_price
                            )

                            # تسجيل حدث مانع للتكرار + إشعار بسيط (اختياري حسب منطق النظام لديك)
                            try:
                                self.repo.update_with_event(rec, event_type, {"price": price, "tp_price": tp_price, "pct": close_percent})
                            except Exception:
                                log.warning("Failed to store partial TP event for rec #%s", rec.id, exc_info=True)

                            action_count += 1
                            # نخرج من الحلقة لتجنب تنفيذ هدفين في نفس "التكة"
                            break

                # --- Intermediate TP notifications (exclude final TP) (السلوك القديم مع دعم الشكلين) ---
                if rec.targets.values:
                    # إذا كان هناك أكثر من هدف، نستثني الأخير من التنبيهات المرحلية
                    targets_to_notify = rec.targets.values if len(rec.targets.values) == 1 else rec.targets.values[:-1]
                    for i, tp_raw in enumerate(targets_to_notify, start=1):
                        tp_price, _ = self._extract_tp_components(tp_raw)
                        event_type = f"TP{i}_HIT"
                        if not self.repo.check_if_event_exists(rec.id, event_type):
                            is_tp_hit = (side == "LONG" and price >= tp_price) or (side == "SHORT" and price <= tp_price)
                            if is_tp_hit:
                                log.info(f"TP{i} hit for rec #{rec.id}. Logging event and notifying.")
                                self.repo.update_with_event(rec, event_type, {"price": price, "target": tp_price})
                                note = f"<b>🔥 الهدف #{i} تحقق لـ #{rec.asset.value}!</b>\nالسعر وصل إلى {tp_price:g}."
                                self._notify_all_channels(rec.id, note)
                                action_count += 1

                # --- Near-touch alerts for analyst (one-time) (السلوك القديم مع دعم الشكلين) ---
                if near_alert_pct > 0:
                    near_sl_event = "NEAR_SL_ALERT"
                    if not self.repo.check_if_event_exists(rec.id, near_sl_event):
                        is_near_sl = (side == "LONG" and sl < price <= sl * (1 + near_alert_pct)) or \
                                     (side == "SHORT" and sl > price >= sl * (1 - near_alert_pct))
                        if is_near_sl:
                            log.info("Near SL for rec #%s. Logging event and notifying analyst.", rec.id)
                            self.repo.update_with_event(rec, near_sl_event, {"price": price, "sl": sl})
                            self._notify_private(rec, f"⏳ اقتراب من وقف الخسارة لـ {rec.asset.value}: السعر={price:g} ~ الوقف={sl:g}")
                            action_count += 1

                    near_tp1_event = "NEAR_TP1_ALERT"
                    if rec.targets.values and not self.repo.check_if_event_exists(rec.id, near_tp1_event):
                        tp1_raw = rec.targets.values[0]
                        tp1, _ = self._extract_tp_components(tp1_raw)
                        is_near_tp1 = (side == "LONG" and tp1 > price >= tp1 * (1 - near_alert_pct)) or \
                                      (side == "SHORT" and tp1 < price <= tp1 * (1 + near_alert_pct))
                        if is_near_tp1:
                            log.info("Near TP1 for rec #%s. Logging event and notifying analyst.", rec.id)
                            self.repo.update_with_event(rec, near_tp1_event, {"price": price, "tp1": tp1})
                            self._notify_private(rec, f"⏳ اقتراب من الهدف الأول لـ {rec.asset.value}: السعر={price:g} ~ الهدف={tp1:g}")
                            action_count += 1

            except Exception as e:
                log.exception("Alert check error for rec=%s: %s", rec.id, e)

        return action_count

    # ----------------- Notification helpers -----------------
    def _notify_private(self, rec: Recommendation, text: str):
        uid = _parse_int_user_id(rec.user_id)
        if not uid:
            return
        try:
            self.notifier.send_private_text(chat_id=uid, text=text)
        except Exception:
            log.warning("Failed to send private alert for rec #%s: '%s'", rec.id, text, exc_info=True)

    def _notify_all_channels(self, rec_id: int, text: str):
        published_messages = self.repo.get_published_messages(rec_id)
        for msg_meta in published_messages:
            try:
                self.notifier.post_notification_reply(
                    chat_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    text=text
                )
            except Exception:
                log.warning(
                    "Failed to send multi-channel notification for rec #%s to channel %s",
                    rec_id, msg_meta.telegram_channel_id, exc_info=True
                )
#end