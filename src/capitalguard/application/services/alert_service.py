# --- START OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.3) ---
# src/capitalguard/application/services/alert_service.py

import logging
import os
import asyncio
from typing import Optional, List, Dict, Set

from sqlalchemy.orm import Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.domain.value_objects import Target  # ✅ CRITICAL FIX: Import Target from value_objects
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.pricing.binance import BinancePricing
from capitalguard.infrastructure.db.base import SessionLocal

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

class AlertService:
    """
    A background service that periodically checks all active recommendations
    for conditions that trigger automated actions or notifications.
    """
    def __init__(self, price_service: PriceService, notifier: any, repo: RecommendationRepository, trade_service: TradeService):
        self.price_service = price_service
        self.notifier = notifier
        self.repo = repo
        self.trade_service = trade_service

    def schedule_job(self, app, interval_sec: int = 5):
        """Schedules the periodic check to run via the bot's job queue."""
        jq = getattr(app, "job_queue", None)
        if jq is None:
            log.warning("JobQueue not available; skipping alert scheduling.")
            return
        try:
            jq.run_repeating(self._job_callback, interval=interval_sec, first=15)
            log.info("Alert job scheduled to run every %ss", interval_sec)
        except Exception as e:
            log.error("Failed to schedule the alert job: %s", e, exc_info=True)

    async def _job_callback(self, context):
        """
        The async callback executed by the JobQueue. It runs the synchronous
        `check_once` method in a separate thread to avoid blocking the main event loop.
        """
        try:
            loop = asyncio.get_running_loop()
            num_actions = await loop.run_in_executor(None, self.check_once)
            if num_actions and num_actions > 0:
                log.info("Alert job finished, triggered %d actions.", num_actions)
        except Exception as e:
            log.exception("An unhandled exception occurred in the alert job callback: %s", e)

    @staticmethod
    def _extract_tp_price(target: Target) -> float:
        """Safely extracts the price from a Target value object."""
        return float(target.price)

    def check_once(self) -> int:
        """
        Performs a single, comprehensive check of all active recommendations.
        This method is synchronous and designed to be run in a background thread.
        It uses `asyncio.run()` safely to call the async methods of the TradeService.
        """
        action_count = 0
        
        with SessionLocal() as db_session:
            try:
                active_recs = self.repo.list_open(db_session)
                if not active_recs:
                    return 0

                symbols_to_check = {rec.asset.value for rec in active_recs if rec.status == RecommendationStatus.ACTIVE}
                if not symbols_to_check:
                    return 0

                price_map = BinancePricing.get_all_prices(spot=False)
                if not price_map:
                    log.warning("Could not fetch bulk prices from Binance for alert check.")
                    return 0

                rec_ids = [rec.id for rec in active_recs if rec.id is not None]
                events_map = self.repo.get_events_for_recommendations(db_session, rec_ids)

                auto_close_enabled = _env_bool("AUTO_CLOSE_ENABLED", False)
                
                for rec in active_recs:
                    if rec.status != RecommendationStatus.ACTIVE:
                        continue

                    price = price_map.get(rec.asset.value)
                    if price is None:
                        continue

                    try:
                        self.trade_service.update_price_tracking(rec.id, price)
                        side = rec.side.value.upper()
                        
                        # --- Auto-Close Logic ---
                        if auto_close_enabled and rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP and rec.targets.values:
                            last_tp_price = self._extract_tp_price(rec.targets.values[-1])
                            if (side == "LONG" and price >= last_tp_price) or (side == "SHORT" and price <= last_tp_price):
                                log.info(f"Auto-closing rec #{rec.id} due to final TP hit at price {price}.")
                                asyncio.run(self.trade_service.close_recommendation_for_user_async(rec.id, rec.user_id, price, reason="FINAL_TP_HIT"))
                                action_count += 1
                                continue
                        
                        # --- Intermediate Target Hit & Partial Profit Logic ---
                        if rec.targets.values:
                            rec_events = events_map.get(rec.id, set())
                            for i, target in enumerate(rec.targets.values):
                                event_type_hit = f"TP{i+1}_HIT"
                                if event_type_hit not in rec_events:
                                    is_tp_hit = (side == "LONG" and price >= target.price) or (side == "SHORT" and price <= target.price)
                                    if is_tp_hit:
                                        log.info(f"TP{i+1} hit for rec #{rec.id}. Logging event and notifying.")
                                        # This action is complex and delegated to the trade service
                                        asyncio.run(self.trade_service.process_target_hit_async(rec.id, rec.user_id, i + 1, price))
                                        action_count += 1
                                        break # Process one target hit per cycle for atomicity

                    except Exception as e:
                        log.exception("Inner alert check loop failed for recommendation ID #%s: %s", rec.id, e)
                
            except Exception as e:
                log.exception("Outer alert check loop failed: %s", e)

        return action_count

# --- END OF FINAL, FULLY CORRECTED AND ROBUST FILE (Version 8.1.2) ---