# --- START OF FILE: src/capitalguard/application/services/alert_service.py ---
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Set, Tuple, List, Optional
import logging, os
from telegram.ext import Application
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

def _env_bool(name: str, default: bool=False) -> bool:
    v = os.getenv(name)
    if v is None: return default
    return str(v).strip().lower() in ("1","true","yes","on")

def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except Exception:
        return default

@dataclass
class AlertService:
    """
    - Near-touch alerts
    - Trailing Stop: نقل SL إلى BE عند تحقق TP1
    - Auto-Close: إغلاق تلقائي عند SL أو آخر TP
    * لا يغير المخطط؛ يستخدم trade_service لتحديث/إغلاق ونشر البطاقة.
    """
    price_service: PriceService
    notifier: any
    repo: any
    trade_service: any

    _alerted: Set[Tuple[int, str, int]] = field(default_factory=set)  # (rec_id, kind['TP'/'SL'/'NEAR'], idx)
    _trailing_applied: Set[int] = field(default_factory=set)

    def schedule_job(self, app: Application, interval_sec: int = 30):
        app.job_queue.run_repeating(self._job, interval=interval_sec, first=10)
        app.bot_data.setdefault("services", {})

    async def _job(self, ctx):
        try:
            n = self.check_once()
            if n:
                log.info("Alert job: %s actions", n)
        except Exception as e:
            log.warning("Alert job exception: %s", e)

    def check_once(self) -> int:
        count = 0
        items = [r for r in self.repo.list_all() if str(r.status).upper() == "OPEN"]
        auto_close   = _env_bool("AUTO_CLOSE_ENABLED", False)
        trailing_en  = _env_bool("TRAILING_STOP_ENABLED", True)
        near_pct     = _env_float("NEAR_ALERT_PCT", 1.5)

        for rec in items:
            try:
                asset = getattr(rec.asset, "value", rec.asset)
                market = getattr(rec, "market", "Spot")
                price = self.price_service.get_preview_price(asset, getattr(market, "value", market))
                if price is None:
                    continue
                side = getattr(rec.side, "value", rec.side).upper()
                entry = float(getattr(rec.entry, "value", rec.entry))
                sl    = float(getattr(rec.stop_loss, "value", rec.stop_loss))
                tps   = list(getattr(rec.targets, "values", rec.targets or []))
                last_tp = float(tps[-1]) if tps else None

                # Trailing → BE عند تحقق TP1
                if trailing_en and tps:
                    tp1 = float(tps[0])
                    tp_hit = (side == "LONG" and price >= tp1) or (side == "SHORT" and price <= tp1)
                    if tp_hit and rec.id not in self._trailing_applied:
                        new_sl = entry
                        try:
                            rec2 = self.trade_service.update_sl(rec.id, new_sl, publish=True)
                            self._trailing_applied.add(rec.id)
                            count += 1
                            self._notify(f"🔄 Trailing SL → BE for {asset} (rec #{rec.id})")
                        except Exception as e:
                            log.warning("trailing update failed rec=%s: %s", rec.id, e)

                # Near-touch
                if near_pct > 0:
                    dist_sl = abs((price - sl) / entry) * 100.0 if entry else 0.0
                    if dist_sl <= near_pct:
                        key = (rec.id, "NEAR", 0)
                        if key not in self._alerted:
                            self._alerted.add(key)
                            self._notify(f"⏳ Near SL {asset}: price={price:g} ~ SL={sl:g} (rec #{rec.id})")
                            count += 1
                    if tps:
                        tp1 = float(tps[0])
                        dist_tp1 = abs((tp1 - price) / entry) * 100.0 if entry else 0.0
                        if dist_tp1 <= near_pct:
                            key = (rec.id, "NEAR", 1)
                            if key not in self._alerted:
                                self._alerted.add(key)
                                self._notify(f"⏳ Near TP1 {asset}: price={price:g} ~ TP1={tp1:g} (rec #{rec.id})")
                                count += 1

                # Auto-Close
                if auto_close:
                    if (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl):
                        self._close(rec, price, reason="SL hit"); count += 1; continue
                    if last_tp is not None:
                        if (side == "LONG" and price >= last_tp) or (side == "SHORT" and price <= last_tp):
                            self._close(rec, price, reason="Final TP hit"); count += 1; continue

            except Exception as e:
                log.warning("alert check error rec=%s: %s", getattr(rec, "id", "?"), e)
        return count

    def _notify(self, text: str):
        try:
            self.notifier._post("sendMessage", {
                "chat_id": int(self.notifier.settings.TELEGRAM_CHAT_ID),
                "text": text
            })
        except Exception:
            pass

    def _close(self, rec, price: float, reason: str):
        try:
            rec2 = self.trade_service.close(rec.id, price)
            if hasattr(self.notifier, "publish_or_update"):
                self.notifier.publish_or_update(rec2)
            self._notify(f"✅ Auto-Closed #{rec.id} ({reason}) @ {price:g}")
        except Exception as e:
            log.warning("auto-close failed rec=%s: %s", getattr(rec, "id", "?"), e)
# --- END OF FILE ---