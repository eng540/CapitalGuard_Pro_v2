import logging
import os
import asyncio
from datetime import datetime, timezone
from typing import Optional, List, Dict, Set, Any

from sqlalchemy.orm import Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.db.base import SessionLocal

log = logging.getLogger(__name__)

# -------------------------
# Environment helpers
# -------------------------
def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return str(v).strip().lower() in ("1", "true", "yes", "on") if v is not None else default

def _env_float(name: str, default: float = 0.0) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default

def _parse_int_user_id(user_id: Optional[Any]) -> Optional[int]:
    """
    Accept int or string containing integer. Return None for invalid values.
    """
    if user_id is None:
        return None
    try:
        return int(str(user_id).strip())
    except (TypeError, ValueError):
        return None

# -------------------------
# Alert Service
# -------------------------
class AlertService:
    """
    Robust AlertService implementation:
      - Uses PriceService (preferred) for pricing; falls back to Binance bulk if necessary.
      - Runs check_once inside a thread (via run_in_executor) so telegram event loop isn't blocked.
      - Uses an isolated DB session for the entire check cycle and commits/rolls back explicitly.
      - Interval for scheduling is configurable via ALERT_JOB_INTERVAL_SEC (default 10s).
      - Emits lightweight telemetry (duration) and guards calls to repo/trade_service that accept session.
    """

    def __init__(
        self,
        price_service: PriceService,
        notifier: Any,
        repo: RecommendationRepository,
        trade_service: TradeService,
    ):
        self.price_service = price_service
        self.notifier = notifier
        self.repo = repo
        self.trade_service = trade_service

    # -------------------------
    # Scheduling
    # -------------------------
    def schedule_job(self, app, interval_sec: Optional[int] = None):
        jq = getattr(app, "job_queue", None)
        if jq is None:
            log.warning("JobQueue not available; skipping alert scheduling.")
            return
        try:
            interval = interval_sec or _env_int("ALERT_JOB_INTERVAL_SEC", 10)
            jq.run_repeating(self._job_callback, interval=interval, first=15)
            log.info("Alert job scheduled to run every %ss", interval)
        except Exception as e:
            log.error("Failed to schedule the alert job: %s", e, exc_info=True)

    async def _job_callback(self, context):
        try:
            loop = asyncio.get_running_loop()
            num_actions = await loop.run_in_executor(None, self.check_once)
            if num_actions and num_actions > 0:
                log.info("Alert job finished, triggered %d actions.", num_actions)
        except Exception as e:
            log.exception("An unhandled exception occurred in the alert job callback: %s", e)

    # -------------------------
    # Utilities
    # -------------------------
    @staticmethod
    def _extract_tp_price(tp) -> float:
        try:
            return float(getattr(tp, "price", tp))
        except Exception:
            return float(tp)

    def _safe_call_with_session(self, func, *, session: Session = None, **kwargs):
        """
        Helper: attempt to call func with session= if it supports it, otherwise call without.
        This keeps compatibility with older implementations that don't accept `session`.
        """
        try:
            # Prefer to pass session if provided
            if session is not None:
                return func(session=session, **kwargs)
        except TypeError:
            # function doesn't accept session kwarg
            return func(**kwargs)
        # If session is None or above returned None, call without session
        return func(**kwargs)

    def _build_price_map(self, symbols: Set[str], recs: List[Recommendation]) -> Dict[str, float]:
        """
        Build a map symbol -> price.
        Prefer PriceService.get_cached_price_sync for each symbol; if PriceService lacks
        a blocking interface or fails, fall back to BinancePricing.get_all_prices.
        """
        price_map: Dict[str, float] = {}

        if not symbols:
            return price_map

        # Try to fetch per-symbol via PriceService sync wrapper (safe for thread context)
        for sym in symbols:
            # determine market for this symbol from recs (first match) else default to 'Futures'
            market = "Futures"
            for r in recs:
                if getattr(r, "asset", None) and getattr(r.asset, "value", None) == sym:
                    market = getattr(r, "market", "Futures")
                    break
            try:
                # PriceService should expose a blocking sync method; support multiple possible names for compatibility
                if hasattr(self.price_service, "get_cached_price_sync"):
                    p = self.price_service.get_cached_price_sync(sym, market)
                elif hasattr(self.price_service, "get_preview_price_sync"):
                    p = self.price_service.get_preview_price_sync(sym, market)
                elif hasattr(self.price_service, "get_cached_price_blocking"):
                    p = self.price_service.get_cached_price_blocking(sym, market)
                else:
                    p = None
            except Exception:
                p = None

            if p is not None:
                try:
                    price_map[sym] = float(p)
                except Exception:
                    continue

        # Fallback: if price_map is incomplete, try Binance bulk (spot=False by default)
        missing = symbols - set(price_map.keys())
        if missing:
            try:
                bulk = BinancePricing.get_all_prices(spot=False)
                if bulk:
                    for ms in missing:
                        val = bulk.get(ms) or bulk.get(ms.upper())
                        if val is not None:
                            try:
                                price_map[ms] = float(val)
                            except Exception:
                                continue
            except Exception:
                log.debug("Binance bulk price fallback failed", exc_info=True)

        return price_map

    # -------------------------
    # Main check logic
    # -------------------------
    def check_once(self) -> int:
        """
        Single cycle run:
          - Use an isolated DB session for the whole cycle to avoid locking issues.
          - Fetch open recommendations, build price map, fetch events map, then run the closing/alert logic.
        """
        started_at = datetime.now(timezone.utc)
        action_count = 0

        # Use an explicit session context so we can commit/rollback at top-level of the cycle.
        db_session: Optional[Session] = None
        try:
            db_session = SessionLocal()

            # 1) load active/pending recs using repo with session
            try:
                active_recs: List[Recommendation] = self._safe_call_with_session(self.repo.list_open, session=db_session)
            except Exception as e:
                log.exception("Failed to list open recommendations: %s", e)
                return 0

            if not active_recs:
                return 0

            # 2) identify unique symbols (only ACTIVE ones)
            unique_symbols: Set[str] = {rec.asset.value for rec in active_recs if getattr(rec, "status", None) == RecommendationStatus.ACTIVE}
            if not unique_symbols:
                return 0

            # 3) build price map (prefer PriceService)
            price_map = self._build_price_map(unique_symbols, active_recs)
            if not price_map:
                log.warning("Price map is empty; skipping this check cycle.")
                return 0

            # 4) fetch events map for active recs
            active_rec_ids = [rec.id for rec in active_recs if rec.id is not None]
            try:
                events_map = self._safe_call_with_session(
                    getattr(self.repo, "get_events_for_recommendations"),
                    session=db_session,
                    rec_ids=active_rec_ids,
                )
            except Exception:
                # final fallback: build minimal events_map via check_if_event_exists (more queries)
                events_map = {}
                for rid in active_rec_ids:
                    try:
                        rows = self._safe_call_with_session(self.repo.get_events_for_recommendations, session=db_session, rec_ids=[rid])
                        events_map[rid] = rows.get(rid, set()) if isinstance(rows, dict) else set()
                    except Exception:
                        events_map[rid] = set()

            # 5) configuration flags
            auto_close_enabled = _env_bool("AUTO_CLOSE_ENABLED", False)
            near_alert_pct = _env_float("NEAR_ALERT_PCT", 1.5) / 100.0

            # 6) iterate recs and apply rules
            for rec in active_recs:
                if getattr(rec, "status", None) != RecommendationStatus.ACTIVE:
                    continue

                sym = getattr(rec.asset, "value", None)
                if not sym:
                    continue

                price = price_map.get(sym)
                if price is None:
                    # price not available for this symbol; skip
                    continue

                try:
                    # update price tracking (best-effort)
                    try:
                        self._safe_call_with_session(self.trade_service.update_price_tracking, session=db_session, rec_id=rec.id, price=price)
                    except Exception:
                        log.debug("update_price_tracking failed for rec %s (non-fatal)", rec.id)

                    side = getattr(rec.side, "value", str(getattr(rec, "side", "")).upper()).upper()
                    rec_events: Set[str] = events_map.get(rec.id, set()) if isinstance(events_map, dict) else set()

                    # 6.1 Stop Loss priority
                    sl = getattr(rec.stop_loss, "value", None)
                    if sl is not None and ((side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)):
                        log.warning("Auto-closing rec #%s due to SL hit at price %s.", rec.id, price)
                        try:
                            self._safe_call_with_session(self.trade_service.close, session=db_session, rec_id=rec.id, exit_price=price, reason="SL_HIT")
                        except Exception:
                            log.exception("Failed to close rec %s on SL_HIT", rec.id)
                        action_count += 1
                        continue

                    # 6.2 Profit Stop
                    profit_stop = getattr(rec, "profit_stop_price", None)
                    if profit_stop is not None and ((side == "LONG" and price <= profit_stop) or (side == "SHORT" and price >= profit_stop)):
                        log.info("Auto-closing rec #%s due to Profit Stop hit at price %s.", rec.id, price)
                        try:
                            self._safe_call_with_session(self.trade_service.close, session=db_session, rec_id=rec.id, exit_price=price, reason="PROFIT_STOP_HIT")
                        except Exception:
                            log.exception("Failed to close rec %s on PROFIT_STOP_HIT", rec.id)
                        action_count += 1
                        continue

                    # 6.3 Final TP if auto-close enabled
                    if auto_close_enabled and getattr(rec, "exit_strategy", None) == ExitStrategy.CLOSE_AT_FINAL_TP and getattr(rec.targets, "values", None):
                        last_tp_price = self._extract_tp_price(rec.targets.values[-1])
                        if (side == "LONG" and price >= last_tp_price) or (side == "SHORT" and price <= last_tp_price):
                            log.info("Auto-closing rec #%s due to final TP hit at price %s.", rec.id, price)
                            try:
                                self._safe_call_with_session(self.trade_service.close, session=db_session, rec_id=rec.id, exit_price=price, reason="FINAL_TP_HIT")
                            except Exception:
                                log.exception("Failed to close rec %s on FINAL_TP_HIT", rec.id)
                            action_count += 1
                            continue

                    # 6.4 TP hits and partial profit
                    if getattr(rec.targets, "values", None):
                        for i, target in enumerate(rec.targets.values):
                            evt_hit = f"TP{i+1}_HIT"
                            if evt_hit in rec_events:
                                continue
                            tp_price = getattr(target, "price", None) if target is not None else None
                            if tp_price is None:
                                continue
                            is_hit = (side == "LONG" and price >= tp_price) or (side == "SHORT" and price <= tp_price)
                            if is_hit:
                                log.info("TP%d hit for rec #%s. Logging event + notify + update cards.", i+1, rec.id)
                                try:
                                    updated_rec = self._safe_call_with_session(self.repo.update_with_event, session=db_session, rec=rec, event_type=evt_hit, event_data={"price": price, "target": tp_price})
                                except Exception:
                                    log.exception("Failed repo.update_with_event for rec %s", rec.id)
                                    updated_rec = rec  # fallback

                                # Notify channels
                                note = f"🔥 تم بلوغ الهدف {i+1} | إشارة #{rec.id}\nالرمز **{rec.asset.value}** وصل إلى **{tp_price:g}**."
                                try:
                                    self._safe_call_with_session(self._notify_all_channels, session=db_session, rec_id=rec.id, text=note)
                                except Exception:
                                    log.debug("Channel notify failed (non-fatal) for rec %s", rec.id)

                                # Update cards (best-effort)
                                try:
                                    self._safe_call_with_session(self.trade_service._update_all_cards, session=db_session, rec=updated_rec)
                                except Exception:
                                    log.debug("update_all_cards failed (non-fatal) for rec %s", rec.id)

                                action_count += 1

                                # Partial close if defined
                                close_pct = getattr(target, "close_percent", 0) or 0
                                if close_pct > 0:
                                    try:
                                        self._safe_call_with_session(self.trade_service.take_partial_profit, session=db_session, rec_id=rec.id, closed_percent=close_pct, price=tp_price, triggered_by="AUTO")
                                    except Exception:
                                        log.exception("take_partial_profit failed for rec %s at TP %d", rec.id, i+1)
                                    action_count += 1

                                # break after first new TP hit in this cycle
                                break

                    # 6.5 Near alerts
                    if near_alert_pct > 0:
                        near_sl_evt = "NEAR_SL_ALERT"
                        if near_sl_evt not in rec_events:
                            try:
                                sl = getattr(rec.stop_loss, "value", None)
                                if sl is not None:
                                    is_near_sl = (side == "LONG" and sl < price <= sl * (1 + near_alert_pct)) or (side == "SHORT" and sl > price >= sl * (1 - near_alert_pct))
                                    if is_near_sl:
                                        try:
                                            self._safe_call_with_session(self.repo.update_with_event, session=db_session, rec=rec, event_type=near_sl_evt, event_data={"price": price, "sl": sl})
                                        except Exception:
                                            log.debug("update_with_event for NEAR_SL failed (non-fatal) for rec %s", rec.id)
                                        self._notify_private(rec, f"⏳ اقتراب من وقف الخسارة لـ {rec.asset.value}: السعر={price:g} ~ الوقف={sl:g}")
                                        action_count += 1
                            except Exception:
                                log.debug("Error evaluating NEAR_SL for rec %s", rec.id)

                        # Near TP1
                        if getattr(rec.targets, "values", None):
                            near_tp1_evt = "NEAR_TP1_ALERT"
                            if near_tp1_evt not in rec_events:
                                try:
                                    tp1_price = self._extract_tp_price(rec.targets.values[0])
                                    is_near_tp1 = (side == "LONG" and tp1_price > price >= tp1_price * (1 - near_alert_pct)) or (side == "SHORT" and tp1_price < price <= tp1_price * (1 + near_alert_pct))
                                    if is_near_tp1:
                                        try:
                                            self._safe_call_with_session(self.repo.update_with_event, session=db_session, rec=rec, event_type=near_tp1_evt, event_data={"price": price, "tp1": tp1_price})
                                        except Exception:
                                            log.debug("update_with_event for NEAR_TP1 failed (non-fatal) for rec %s", rec.id)
                                        self._notify_private(rec, f"⏳ اقتراب من الهدف الأول لـ {rec.asset.value}: السعر={price:g} ~ الهدف={tp1_price:g}")
                                        action_count += 1
                                except Exception:
                                    log.debug("Error evaluating NEAR_TP1 for rec %s", rec.id)

                except Exception as e:
                    log.exception("Inner alert check loop failed for recommendation ID #%s: %s", rec.id, e)

            # commit at end of cycle
            try:
                db_session.commit()
            except Exception:
                log.exception("Commit at end of alert check cycle failed; rolling back.")
                db_session.rollback()

        except Exception as e:
            log.exception("Outer alert check loop failed: %s", e)
            if db_session is not None:
                try:
                    db_session.rollback()
                except Exception:
                    log.exception("Rollback failed in outer exception handler.")
        finally:
            if db_session is not None:
                try:
                    db_session.close()
                except Exception:
                    log.exception("Failed to close DB session in alert check cycle.")

        # telemetry
        dur = (datetime.now(timezone.utc) - started_at).total_seconds()
        log.debug("Alert check_once finished in %.3fs; actions=%d", dur, action_count)

        return action_count

    # -------------------------
    # Notifications
    # -------------------------
    def _notify_private(self, rec: Recommendation, text: str):
        uid = _parse_int_user_id(getattr(rec, "user_id", None))
        if not uid:
            return
        try:
            self.notifier.send_private_text(chat_id=uid, text=text)
        except Exception:
            log.warning("Failed to send private alert for rec #%s", getattr(rec, "id", "?"), exc_info=True)

    def _notify_all_channels(self, rec_id: int, text: str, session: Optional[Session] = None):
        try:
            published_messages = self._safe_call_with_session(self.repo.get_published_messages, session=session, rec_id=rec_id)
        except Exception:
            published_messages = []

        for msg_meta in (published_messages or []):
            try:
                self.notifier.post_notification_reply(
                    chat_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    text=text
                )
            except Exception:
                log.warning("Failed to send multi-channel notification for rec #%s to channel %s", rec_id, getattr(msg_meta, "telegram_channel_id", "?"), exc_info=True)