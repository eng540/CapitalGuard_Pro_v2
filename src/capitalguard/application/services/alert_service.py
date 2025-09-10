# --- START OF COMPLETE MODIFIED FILE: src/capitalguard/application/services/alert_service.py ---
from __future__ import annotations
from dataclasses import dataclass
import logging
import os
import asyncio
from typing import Optional

from capitalguard.application.services.price_service import PriceService
from capitalguard.domain.entities import RecommendationStatus, Recommendation

log = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """Safely parse a user_id string to int, or return None if invalid."""
    try:
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return None

@dataclass
class AlertService:
    """
    Stateful alert service that uses the database for persistence.
    - Near-touch alerts (one-time).
    - Trailing Stop to BE on TP1 hit (one-time).
    - Auto-Close on SL or final TP.
    - TP hit notifications (one-time per TP).
    """
    price_service: PriceService
    notifier: any
    repo: any
    trade_service: any

    def schedule_job(self, app, interval_sec: int = 60):
        """Schedules the periodic check job if JobQueue is available."""
        jq = getattr(app, "job_queue", None)
        if jq is None:
            log.warning("JobQueue is not available; skipping alert scheduling.")
            return
        try:
            jq.run_repeating(self._job, interval=interval_sec, first=15)
            log.info("Alert job scheduled every %ss", interval_sec)
        except Exception as e:
            log.warning("Failed to schedule alert job: %s", e)

    async def _job(self, context):
        """The callback executed by the JobQueue."""
        try:
            num_actions = await asyncio.to_thread(self.check_once)
            if num_actions and num_actions > 0:
                log.info("Alert job finished, triggered %d actions.", num_actions)
        except Exception as e:
            log.exception("Alert job exception: %s", e)

    def check_once(self) -> int:
        """
        Main logic for checking all active recommendations for alert conditions.
        This is a synchronous method that can perform blocking I/O (DB calls).
        """
        count = 0
        items = self.repo.list_open()

        auto_close = _env_bool("AUTO_CLOSE_ENABLED", False)
        trailing_en = _env_bool("TRAILING_STOP_ENABLED", True)
        near_pct = _env_float("NEAR_ALERT_PCT", 1.5) / 100.0  # Convert to fraction

        for rec in items:
            if rec.status != RecommendationStatus.ACTIVE:
                continue

            try:
                asset, market = rec.asset.value, rec.market
                price = self.price_service.get_preview_price(asset, market)
                if price is None:
                    continue

                side, entry = rec.side.value.upper(), rec.entry.value
                sl, tps = rec.stop_loss.value, rec.targets.values
                
                # --- Trailing Stop Logic (Stateful) ---
                if trailing_en and tps and not rec.alert_meta.get("trailing_applied"):
                    tp1 = tps[0]
                    tp1_hit = (side == "LONG" and price >= tp1) or \
                              (side == "SHORT" and price <= tp1)
                    if tp1_hit:
                        # move_sl_to_be now handles the notification across all channels
                        self.trade_service.move_sl_to_be(rec.id)
                        # We just need to mark it as applied here to prevent re-triggering
                        updated_rec = self.repo.get(rec.id)
                        if updated_rec:
                            updated_rec.alert_meta["trailing_applied"] = True
                            self.repo.update(updated_rec)
                        count += 1

                # --- TP Hit Notification Logic (Stateful & Multi-Channel) ---
                rec_updated_for_tp = False
                if tps:
                    for i, tp in enumerate(tps, start=1):
                        alert_key = f"tp{i}_hit_notified"
                        if not rec.alert_meta.get(alert_key):
                            is_tp_hit = (side == "LONG" and price >= tp) or \
                                        (side == "SHORT" and price <= tp)
                            if is_tp_hit:
                                notification_text = (
                                    f"<b>🔥 الهدف #{i} تحقق لـ #{asset}!</b>\n"
                                    f"السعر وصل إلى {tp:g}."
                                )
                                self._notify_all_channels(rec.id, notification_text)
                                rec.alert_meta[alert_key] = True
                                rec_updated_for_tp = True
                                count += 1
                if rec_updated_for_tp:
                    self.repo.update(rec)

                # --- Near-Touch Logic (for private alerts to analyst) ---
                if near_pct > 0:
                    rec_updated_for_near = False
                    if not rec.alert_meta.get("near_sl_alerted"):
                        is_near = (side == "LONG" and sl < price <= sl * (1 + near_pct)) or \
                                  (side == "SHORT" and sl > price >= sl * (1 - near_pct))
                        if is_near:
                            rec.alert_meta["near_sl_alerted"] = True
                            rec_updated_for_near = True
                            self._notify_private(rec, f"⏳ Near SL {asset}: price={price:g} ~ SL={sl:g}")
                            count += 1
                    
                    if tps and not rec.alert_meta.get("near_tp1_alerted"):
                        tp1 = tps[0]
                        is_near = (side == "LONG" and tp1 > price >= tp1 * (1 - near_pct)) or \
                                  (side == "SHORT" and tp1 < price <= tp1 * (1 + near_pct))
                        if is_near:
                            rec.alert_meta["near_tp1_alerted"] = True
                            rec_updated_for_near = True
                            self._notify_private(rec, f"⏳ Near TP1 {asset}: price={price:g} ~ TP1={tp1:g}")
                            count += 1
                    
                    if rec_updated_for_near:
                        self.repo.update(rec)
                
                # --- Auto-Close Logic ---
                if auto_close:
                    sl_hit = (side == "LONG" and price <= sl) or \
                             (side == "SHORT" and price >= sl)
                    if sl_hit:
                        self._close(rec, price, "SL hit")
                        count += 1
                        continue

                    if tps:
                        last_tp = tps[-1]
                        last_tp_hit = (side == "LONG" and price >= last_tp) or \
                                      (side == "SHORT" and price <= last_tp)
                        if last_tp_hit:
                            self._close(rec, price, "Final TP hit")
                            count += 1
                            continue
            
            except Exception as e:
                log.exception("Alert check error for rec=%s: %s", rec.id, e)
        
        return count

    def _notify_private(self, rec: Recommendation, text: str):
        """Sends a private notification to the analyst."""
        uid = _parse_int_user_id(rec.user_id)
        if not uid: return
        try:
            self.notifier.send_private_message(chat_id=uid, text_header=text, rec=rec)
        except Exception:
            log.warning("Failed to send private alert for rec #%s: '%s'", rec.id, text, exc_info=True)
            
    def _notify_all_channels(self, rec_id: int, text: str):
        """Sends a threaded reply notification to all public channels for a recommendation."""
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

    def _close(self, rec: Recommendation, price: float, reason: str):
        """Handles auto-closing and notifies the analyst."""
        try:
            # The close method in trade_service now handles all public notifications
            self.trade_service.close(rec.id, price)
            self._notify_private(rec, f"✅ Auto-Closed #{rec.id} ({reason}) @ {price:g}")
        except Exception as e:
            log.warning("Auto-close failed for rec=%s: %s", rec.id, e, exc_info=True)
# --- END OF COMPLETE MODIFIED FILE ---