# --- START OF FILE: src/capitalguard/application/services/alert_service.py ---
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
    except Exception:
        return default

@dataclass
class AlertService:
    """
    Stateful alert service that uses the database for persistence.
    - Near-touch alerts (one-time).
    - Trailing Stop to BE on TP1 hit (one-time).
    - Auto-Close on SL or final TP.
    - ✅ NEW: TP hit notifications (one-time per TP).
    """
    price_service: PriceService
    notifier: any
    repo: any
    trade_service: any

    def schedule_job(self, app, interval_sec: int = 30):
        """Schedules the periodic check job if JobQueue is available."""
        jq = getattr(app, "job_queue", None)
        if jq is None:
            log.warning("JobQueue is not available; skipping alert scheduling.")
            return
        try:
            jq.run_repeating(self._job, interval=interval_sec, first=10)
            log.info("Alert job scheduled every %ss", interval_sec)
        except Exception as e:
            log.warning("Failed to schedule alert job: %s", e)

    async def _job(self, context):
        """The callback executed by the JobQueue."""
        try:
            # ✅ FIX: Use asyncio.to_thread to run the synchronous, blocking DB/API
            # code in a separate thread, preventing it from blocking the main async loop.
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
                        self.trade_service.move_sl_to_be(rec.id)
                        # The `move_sl_to_be` call already sends a notification.
                        # We just need to mark it as applied here.
                        updated_rec = self.repo.get(rec.id)
                        updated_rec.alert_meta["trailing_applied"] = True
                        self.repo.update(updated_rec)
                        count += 1
                        # Note: No direct notification from here to avoid duplication.
                        # `trade_service.move_sl_to_be` handles it.

                # --- ✅ NEW: TP Hit Notification Logic (Stateful) ---
                rec_updated_for_tp = False
                if tps and rec.channel_id and rec.message_id:
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
                                self._notify_reply(rec, notification_text)
                                rec.alert_meta[alert_key] = True
                                rec_updated_for_tp = True
                                count += 1
                if rec_updated_for_tp:
                    self.repo.update(rec)
                # --- END OF NEW TP LOGIC ---

                # --- Near-Touch Logic (Stateful & Corrected) ---
                if near_pct > 0:
                    rec_updated_for_near = False
                    if not rec.alert_meta.get("near_sl_alerted"):
                        is_near = (side == "LONG" and sl < price <= sl * (1 + near_pct)) or \
                                  (side == "SHORT" and sl > price >= sl * (1 - near_pct))
                        if is_near:
                            rec.alert_meta["near_sl_alerted"] = True
                            rec_updated_for_near = True
                            self._notify_private(f"⏳ Near SL {asset}: price={price:g} ~ SL={sl:g} (rec #{rec.id})")
                            count += 1
                    
                    if tps and not rec.alert_meta.get("near_tp1_alerted"):
                        tp1 = tps[0]
                        is_near = (side == "LONG" and tp1 > price >= tp1 * (1 - near_pct)) or \
                                  (side == "SHORT" and tp1 < price <= tp1 * (1 + near_pct))
                        if is_near:
                            rec.alert_meta["near_tp1_alerted"] = True
                            rec_updated_for_near = True
                            self._notify_private(f"⏳ Near TP1 {asset}: price={price:g} ~ TP1={tp1:g} (rec #{rec.id})")
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

    def _notify_private(self, text: str):
        """Sends a private notification to the analyst/admin."""
        try:
            # Assuming notifier has channel_id for private alerts; this may need adjustment
            # based on how the main admin chat is configured.
            chat_id = int(os.getenv("TELEGRAM_CHAT_ID")) # Or a dedicated admin chat
            self.notifier._send_text(chat_id=chat_id, text=text)
        except Exception:
            log.warning("Failed to send private alert notification: '%s'", text, exc_info=True)
            
    # ✅ --- NEW HELPER: _notify_reply ---
    def _notify_reply(self, rec: Recommendation, text: str):
        """Sends a threaded reply notification to the public channel."""
        if not rec.channel_id or not rec.message_id:
            return
        try:
            self.notifier.post_notification_reply(
                chat_id=rec.channel_id,
                message_id=rec.message_id,
                text=text
            )
        except Exception:
            log.warning("Failed to send threaded notification for rec #%s: '%s'", rec.id, text, exc_info=True)
    # --- END OF NEW HELPER ---

    def _close(self, rec: Recommendation, price: float, reason: str):
        try:
            # The close method in trade_service now handles the public notification
            self.trade_service.close(rec.id, price)
            self._notify_private(f"✅ Auto-Closed #{rec.id} ({reason}) @ {price:g}")
        except Exception as e:
            log.warning("Auto-close failed for rec=%s: %s", rec.id, e, exc_info=True)
# --- END OF FILE: src/capitalguard/application/services/alert_service.py ---