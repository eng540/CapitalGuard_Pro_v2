# --- START OF FINAL, FULLY CORRECTED AND ROBUST FILE (Version 8.1.3) ---
# src/capitalguard/application/services/alert_service.py

import logging
import os
import asyncio
from typing import Optional, List, Dict, Set

from sqlalchemy.orm import Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
# ✅ FIX: Import Value Objects (including Target) from value_objects.py
from capitalguard.domain.value_objects import Side, Target

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
    def __init__(self, price_service: PriceService, notifier: any, repo: RecommendationRepository, trade_service: TradeService):
        self.price_service = price_service
        self.notifier = notifier
        self.repo = repo
        self.trade_service = trade_service

    def schedule_job(self, app, interval_sec: int = 5):
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
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.check_once)
        except Exception as e:
            log.exception("An unhandled exception occurred in the alert job callback: %s", e)

    def check_once(self) -> None:
        """
        Performs a single, comprehensive check of all active recommendations.
        This method is synchronous and designed to be run in a background thread.
        It uses `asyncio.run()` safely to call the async methods of the TradeService.
        """
        try:
            with SessionLocal() as db_session:
                active_recs = self.repo.list_open(db_session)
                if not active_recs:
                    return

                symbols_to_check = {rec.asset.value for rec in active_recs if rec.status == RecommendationStatus.ACTIVE}
                if not symbols_to_check:
                    return

                price_map = BinancePricing.get_all_prices(spot=False)
                if not price_map:
                    log.warning("Could not fetch bulk prices from Binance for alert check.")
                    return

                for rec in active_recs:
                    if rec.status != RecommendationStatus.ACTIVE:
                        continue

                    price = price_map.get(rec.asset.value)
                    if price is None:
                        continue

                    try:
                        self.trade_service.update_price_tracking(rec.id, price)
                        fresh_rec = self.repo.get(db_session, rec.id)
                        if not fresh_rec or fresh_rec.status != RecommendationStatus.ACTIVE:
                            continue
                        
                        asyncio.run(self._process_single_recommendation(fresh_rec, price))

                    except Exception as e:
                        log.exception("Inner alert check loop failed for recommendation ID #%s: %s", rec.id, e)
        except Exception as e:
            log.exception("Outer alert check loop failed: %s", e)

    async def _process_single_recommendation(self, rec: Recommendation, price: float):
        """Async helper to process closing and notification logic for one recommendation."""
        side = rec.side.value.upper()
        user_id = rec.user_id

        if not user_id:
            log.warning(f"Recommendation #{rec.id} has no user_id, skipping.")
            return

        # 1. Stop Loss Check (Highest Priority)
        if (side == "LONG" and price <= rec.stop_loss.value) or (side == "SHORT" and price >= rec.stop_loss.value):
            log.warning(f"Auto-closing rec #{rec.id} due to SL hit at price {price}.")
            await self.trade_service.close_recommendation_for_user_async(rec.id, user_id, price, reason="SL_HIT")
            return

        # 2. Profit Stop Check
        if rec.profit_stop_price is not None:
            if (side == "LONG" and price <= rec.profit_stop_price) or (side == "SHORT" and price >= rec.profit_stop_price):
                log.info(f"Auto-closing rec #{rec.id} due to Profit Stop hit at price {price}.")
                await self.trade_service.close_recommendation_for_user_async(rec.id, user_id, price, reason="PROFIT_STOP_HIT")
                return

        # 3. Final Target Auto-Close Check
        auto_close_enabled = _env_bool("AUTO_CLOSE_ENABLED", False)
        if auto_close_enabled and rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP and rec.targets.values:
            last_tp_price = rec.targets.values[-1].price
            if (side == "LONG" and price >= last_tp_price) or (side == "SHORT" and price <= last_tp_price):
                log.info(f"Auto-closing rec #{rec.id} due to final TP hit at price {price}.")
                await self.trade_service.close_recommendation_for_user_async(rec.id, user_id, price, reason="FINAL_TP_HIT")
                return
        
        # 4. Intermediate Target Hit & Partial Profit Check
        if rec.targets.values:
            with SessionLocal() as session:
                rec_events = self.repo.get_events_for_recommendations(session, [rec.id]).get(rec.id, set())

            for i, target in enumerate(rec.targets.values):
                event_type_hit = f"TP{i+1}_HIT"
                if event_type_hit not in rec_events:
                    if (side == "LONG" and price >= target.price) or (side == "SHORT" and price <= target.price):
                        log.info(f"TP{i+1} hit for rec #{rec.id}. Processing.")
                        await self.trade_service.process_target_hit_async(rec.id, user_id, i + 1, price)
                        break

# --- END OF FINAL, FULLY CORRECTED AND ROBUST FILE (Version 8.1.3) ---