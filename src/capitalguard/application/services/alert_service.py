# --- START OF MODIFIED FILE: src/capitalguard/application/services/alert_service.py ---
import logging
import os
import asyncio
from typing import Optional

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService # For type hinting
from capitalguard.infrastructure.db.repository import RecommendationRepository # For type hinting

log = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name); return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else default
def _env_float(name: str, default: float) -> float:
    v = os.getenv(name);
    try: return float(v) if v is not None else default
    except (ValueError, TypeError): return default
def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try: return int(user_id) if user_id is not None else None
    except (TypeError, ValueError): return None

class AlertService:
    """
    Event-driven, stateful alert service.
    - Tracks highest/lowest prices.
    - Sends one-time alerts for TP hits and near-touch events by checking the event log.
    - Handles auto-closing based on SL/final TP.
    """
    def __init__(self, price_service: PriceService, notifier: any, repo: RecommendationRepository, trade_service: TradeService):
        self.price_service = price_service
        self.notifier = notifier
        self.repo = repo
        self.trade_service = trade_service

    def schedule_job(self, app, interval_sec: int = 60):
        jq = getattr(app, "job_queue", None)
        if jq is None: log.warning("JobQueue not available; skipping alert scheduling."); return
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

    def check_once(self) -> int:
        action_count = 0
        active_recs = self.repo.list_open()
        auto_close_enabled = _env_bool("AUTO_CLOSE_ENABLED", False)
        near_alert_pct = _env_float("NEAR_ALERT_PCT", 1.5) / 100.0

        for rec in active_recs:
            if rec.status != RecommendationStatus.ACTIVE: continue
            
            try:
                price = self.price_service.get_preview_price(rec.asset.value, rec.market)
                if price is None: continue

                # 1. Update Price Tracking (highest/lowest reached)
                self.trade_service.update_price_tracking(rec.id, price)

                side = rec.side.value.upper()
                
                # 2. TP Hit Notifications (Event-based)
                for i, tp in enumerate(rec.targets.values, start=1):
                    event_type = f"TP{i}_HIT"
                    if not self.repo.check_if_event_exists(rec.id, event_type):
                        is_tp_hit = (side == "LONG" and price >= tp) or (side == "SHORT" and price <= tp)
                        if is_tp_hit:
                            log.info(f"TP{i} hit for rec #{rec.id}. Logging event and notifying.")
                            event_data = {"price": price, "target": tp}
                            # Log event first, then notify
                            self.repo.update_with_event(rec, event_type, event_data)
                            notification_text = f"<b>🔥 الهدف #{i} تحقق لـ #{rec.asset.value}!</b>\nالسعر وصل إلى {tp:g}."
                            self._notify_all_channels(rec.id, notification_text)
                            action_count += 1

                # 3. Near-Touch Alerts (Event-based)
                if near_alert_pct > 0:
                    # Near SL
                    near_sl_event = "NEAR_SL_ALERT"
                    if not self.repo.check_if_event_exists(rec.id, near_sl_event):
                        sl = rec.stop_loss.value
                        is_near_sl = (side == "LONG" and sl < price <= sl * (1 + near_alert_pct)) or \
                                     (side == "SHORT" and sl > price >= sl * (1 - near_alert_pct))
                        if is_near_sl:
                            log.info(f"Near SL for rec #{rec.id}. Logging event and notifying analyst.")
                            self.repo.update_with_event(rec, near_sl_event, {"price": price, "sl": sl})
                            self._notify_private(rec, f"⏳ اقتراب من وقف الخسارة لـ {rec.asset.value}: السعر={price:g} ~ الوقف={sl:g}")
                            action_count += 1
                    
                    # Near TP1
                    near_tp1_event = "NEAR_TP1_ALERT"
                    if rec.targets.values and not self.repo.check_if_event_exists(rec.id, near_tp1_event):
                        tp1 = rec.targets.values[0]
                        is_near_tp1 = (side == "LONG" and tp1 > price >= tp1 * (1 - near_alert_pct)) or \
                                      (side == "SHORT" and tp1 < price <= tp1 * (1 + near_alert_pct))
                        if is_near_tp1:
                            log.info(f"Near TP1 for rec #{rec.id}. Logging event and notifying analyst.")
                            self.repo.update_with_event(rec, near_tp1_event, {"price": price, "tp1": tp1})
                            self._notify_private(rec, f"⏳ اقتراب من الهدف الأول لـ {rec.asset.value}: السعر={price:g} ~ الهدف={tp1:g}")
                            action_count += 1

                # 4. Auto-Close Logic
                if auto_close_enabled:
                    sl = rec.stop_loss.value
                    if (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl):
                        log.warning(f"Auto-closing rec #{rec.id} due to SL hit at price {price}.")
                        self.trade_service.close(rec.id, price)
                        action_count += 1
                        continue # Move to next recommendation
                    
                    if rec.targets.values:
                        last_tp = rec.targets.values[-1]
                        if (side == "LONG" and price >= last_tp) or (side == "SHORT" and price <= last_tp):
                            log.info(f"Auto-closing rec #{rec.id} due to final TP hit at price {price}.")
                            self.trade_service.close(rec.id, price)
                            action_count += 1
                            continue
            
            except Exception as e:
                log.exception("Alert check error for rec=%s: %s", rec.id, e)
        
        return action_count

    def _notify_private(self, rec: Recommendation, text: str):
        uid = _parse_int_user_id(rec.user_id)
        if not uid: return
        try:
            # We send the text directly as the header, no need for the full card here
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
                log.warning("Failed to send multi-channel notification for rec #%s to channel %s", rec_id, msg_meta.telegram_channel_id, exc_info=True)

# --- END OF MODIFIED FILE ---