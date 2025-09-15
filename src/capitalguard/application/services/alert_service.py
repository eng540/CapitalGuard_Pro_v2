#START src/capitalguard/application/services/alert_service.py
import logging
import os
import asyncio
from typing import Optional, List

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger(__name__)

# --- Environment Variable Helpers ---
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
        return int(user_id) if user_id is not None and user_id.isdigit() else None
    except (TypeError, ValueError):
        return None

class AlertService:
    """
    A background service that periodically checks all active recommendations
    against live market prices to trigger alerts and automated actions.
    """
    def __init__(self, price_service: PriceService, notifier: any, repo: RecommendationRepository, trade_service: TradeService):
        self.price_service = price_service
        self.notifier = notifier
        self.repo = repo
        self.trade_service = trade_service

    def schedule_job(self, app, interval_sec: int = 5):
        """Schedules the main check_once job to run repeatedly."""
        jq = getattr(app, "job_queue", None)
        if jq is None:
            log.warning("JobQueue not available on the application; skipping alert scheduling.")
            return
        try:
            jq.run_repeating(self._job_callback, interval=interval_sec, first=15)
            log.info("Alert job scheduled to run every %ss", interval_sec)
        except Exception as e:
            log.error("Failed to schedule the alert job: %s", e, exc_info=True)

    async def _job_callback(self, context):
        """Async wrapper for the synchronous check_once method."""
        try:
            loop = asyncio.get_running_loop()
            num_actions = await loop.run_in_executor(None, self.check_once)
            if num_actions and num_actions > 0:
                log.info("Alert job finished, triggered %d actions.", num_actions)
        except Exception as e:
            log.exception("An unhandled exception occurred in the alert job callback: %s", e)

    @staticmethod
    def _extract_tp_price(tp) -> float:
        """Helper to safely extract price from a Target value object."""
        return float(getattr(tp, "price", tp))

    def check_once(self) -> int:
        """
        Performs a single, comprehensive check of all open recommendations.
        ✅ REFACTORED: This method is now highly efficient, using only two DB queries
        regardless of the number of active recommendations, solving the N+1 problem.
        """
        action_count = 0
        active_recs = self.repo.list_open()
        if not active_recs:
            return 0

        auto_close_enabled = _env_bool("AUTO_CLOSE_ENABLED", False)
        near_alert_pct = _env_float("NEAR_ALERT_PCT", 1.5) / 100.0

        active_rec_ids = [rec.id for rec in active_recs if rec.id is not None]
        events_map = self.repo.get_events_for_recommendations(active_rec_ids)

        for rec in active_recs:
            if rec.status != RecommendationStatus.ACTIVE:
                continue

            try:
                price = self.price_service.get_cached_price_blocking(rec.asset.value, rec.market)
                if price is None:
                    continue

                self.trade_service.update_price_tracking(rec.id, price)
                side = rec.side.value.upper()
                rec_events = events_map.get(rec.id, set())

                if (side == "LONG" and price <= rec.stop_loss.value) or (side == "SHORT" and price >= rec.stop_loss.value):
                    log.warning(f"Auto-closing rec #{rec.id} due to SL hit at price {price}.")
                    self.trade_service.close(rec.id, price, reason="SL_HIT")
                    action_count += 1
                    continue

                if rec.profit_stop_price is not None:
                    if (side == "LONG" and price <= rec.profit_stop_price) or \
                       (side == "SHORT" and price >= rec.profit_stop_price):
                        log.info(f"Auto-closing rec #{rec.id} due to Profit Stop hit at price {price}.")
                        self.trade_service.close(rec.id, price, reason="PROFIT_STOP_HIT")
                        action_count += 1
                        continue

                if auto_close_enabled and rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP and rec.targets.values:
                    last_tp_price = self._extract_tp_price(rec.targets.values[-1])
                    if (side == "LONG" and price >= last_tp_price) or (side == "SHORT" and price <= last_tp_price):
                        log.info(f"Auto-closing rec #{rec.id} due to final TP hit at price {price}.")
                        self.trade_service.close(rec.id, price, reason="FINAL_TP_HIT")
                        action_count += 1
                        continue

                if rec.targets.values:
                    for i, target in enumerate(rec.targets.values):
                        event_type_hit = f"TP{i+1}_HIT"
                        if event_type_hit not in rec_events:
                            is_tp_hit = (side == "LONG" and price >= target.price) or (side == "SHORT" and price <= target.price)
                            if is_tp_hit:
                                log.info(f"TP{i+1} hit for rec #{rec.id}. Logging event and notifying.")
                                updated_rec = self.repo.update_with_event(rec, event_type_hit, {"price": price, "target": target.price})
                                note = f"🔥 **Target {i+1} Hit!** | **{rec.asset.value}** reached **{target.price:g}**."
                                self._notify_all_channels(rec.id, note)
                                self.trade_service._update_all_cards(updated_rec)
                                action_count += 1
                                if target.close_percent > 0:
                                    log.info(f"Auto partial profit triggered for rec #{rec.id} at TP{i+1}.")
                                    self.trade_service.take_partial_profit(rec.id, target.close_percent, target.price, triggered_by="AUTO")
                                    action_count += 1
                                break

                if near_alert_pct > 0:
                    near_sl_event = "NEAR_SL_ALERT"
                    if near_sl_event not in rec_events:
                        is_near_sl = (side == "LONG" and rec.stop_loss.value < price <= rec.stop_loss.value * (1 + near_alert_pct)) or \
                                     (side == "SHORT" and rec.stop_loss.value > price >= rec.stop_loss.value * (1 - near_alert_pct))
                        if is_near_sl:
                            self.repo.update_with_event(rec, near_sl_event, {"price": price, "sl": rec.stop_loss.value})
                            self._notify_private(rec, f"⏳ Approaching Stop Loss for {rec.asset.value}: Price={price:g}")
                            action_count += 1
                    
                    near_tp1_event = "NEAR_TP1_ALERT"
                    if rec.targets.values and near_tp1_event not in rec_events:
                        tp1_price = self._extract_tp_price(rec.targets.values[0])
                        is_near_tp1 = (side == "LONG" and tp1_price > price >= tp1_price * (1 - near_alert_pct)) or \
                                      (side == "SHORT" and tp1_price < price <= tp1_price * (1 + near_alert_pct))
                        if is_near_tp1:
                            self.repo.update_with_event(rec, near_tp1_event, {"price": price, "tp1": tp1_price})
                            self._notify_private(rec, f"⏳ Approaching Target 1 for {rec.asset.value}: Price={price:g}")
                            action_count += 1

            except Exception as e:
                log.exception("Alert check failed for recommendation ID #%s: %s", rec.id, e)

        return action_count

    def _notify_private(self, rec: Recommendation, text: str):
        uid = _parse_int_user_id(rec.user_id)
        if not uid: return
        try:
            self.notifier.send_private_text(chat_id=uid, text=text)
        except Exception:
            log.warning("Failed to send private alert for rec #%s", rec.id, exc_info=True)
            
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
                log.warning("Failed to send multi-channel notification for rec #%s to channel %s", rec_id, msg_meta.telegram_channel_id, exc_info=True)