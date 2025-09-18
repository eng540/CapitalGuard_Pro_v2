# --- START OF FINAL, RE-ARCHITECTED AND PRODUCTION-READY FILE (Version 8.1.0) ---
# src/capitalguard/application/services/trade_service.py

import logging
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import asyncio

from sqlalchemy.orm import Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import NotifierPort
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

log = logging.getLogger(__name__)

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """Safely parses a string user ID to an integer."""
    try:
        return int(user_id) if user_id is not None and user_id.strip().isdigit() else None
    except (TypeError, ValueError):
        return None

class TradeService:
    """
    Core application service for managing the lifecycle of trade recommendations.
    """
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: NotifierPort,
        market_data_service: MarketDataService,
        price_service: PriceService,
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service

    # --- Private Helper Methods ---

    async def _update_all_cards_async(self, session: Session, rec: Recommendation):
        """Asynchronously updates all published Telegram messages for a recommendation."""
        published_messages = self.repo.get_published_messages(session, rec.id)
        if not published_messages:
            return

        log.info("Asynchronously updating %d cards for rec #%s...", len(published_messages), rec.id)
        
        update_tasks = []
        for msg_meta in published_messages:
            task = asyncio.to_thread(
                self.notifier.edit_recommendation_card_by_ids,
                channel_id=msg_meta.telegram_channel_id,
                message_id=msg_meta.telegram_message_id,
                rec=rec
            )
            update_tasks.append(task)
        
        results = await asyncio.gather(*update_tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                msg_meta = published_messages[i]
                log.warning(f"Failed to update card for rec #{rec.id} in channel {msg_meta.telegram_channel_id}: {result}")

    def _notify_all_channels(self, session: Session, rec_id: int, text: str):
        """Sends a reply notification to all channels where a recommendation was published."""
        published_messages = self.repo.get_published_messages(session, rec_id)
        for msg_meta in published_messages:
            try:
                self.notifier.post_notification_reply(
                    chat_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    text=text
                )
            except Exception as e:
                log.warning(f"Failed to send reply notification for rec #{rec_id} to channel {msg_meta.telegram_channel_id}: {e}")

    def _validate_recommendation_data(self, side: str, entry: float, stop_loss: float, targets: List[Dict[str, float]]):
        """Centralized validation for core recommendation business rules."""
        side_upper = side.upper()
        if side_upper == "LONG" and not (stop_loss < entry):
            raise ValueError("For new LONG trades, Stop Loss must be < Entry Price.")
        if side_upper == "SHORT" and not (stop_loss > entry):
            raise ValueError("For new SHORT trades, Stop Loss must be > Entry Price.")

        targets_vo = Targets(targets)
        for target in targets_vo.values:
            if (side_upper == 'LONG' and target.price <= entry) or \
               (side_upper == 'SHORT' and target.price >= entry):
                raise ValueError(f"Target price {target.price} is not valid for a {side} trade with entry {entry}.")

    # --- Public Service Methods ---
    
    def get_recommendation_for_user(self, rec_id: int, user_telegram_id: str) -> Optional[Recommendation]:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID format.")
        with SessionLocal() as session:
            return self.repo.get_by_id_for_user(session, rec_id, uid_int)

    async def create_and_publish_recommendation_async(self, **kwargs) -> Tuple[Recommendation, Dict]:
        uid_int = _parse_int_user_id(kwargs.get('user_id'))
        if not uid_int: raise ValueError("A valid user_id is required.")

        asset = kwargs['asset'].strip().upper()
        market = kwargs.get('market', 'Futures')
        if not self.market_data_service.is_valid_symbol(asset, market):
            raise ValueError(f"The symbol '{asset}' is not valid or available in the '{market}' market.")

        order_type_enum = OrderType(kwargs['order_type'].upper())
        status, final_entry = (RecommendationStatus.PENDING, kwargs['entry'])
        if order_type_enum == OrderType.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            if live_price is None: raise RuntimeError(f"Could not fetch live price for {asset}.")
            status, final_entry = RecommendationStatus.ACTIVE, live_price

        self._validate_recommendation_data(kwargs['side'], final_entry, kwargs['stop_loss'], kwargs['targets'])
        
        with SessionLocal() as session:
            try:
                rec_entity = Recommendation(
                    asset=Symbol(asset), side=Side(kwargs['side']), entry=Price(final_entry),
                    stop_loss=Price(kwargs['stop_loss']), targets=Targets(kwargs['targets']),
                    order_type=order_type_enum, status=status, market=market, notes=kwargs.get('notes'),
                    user_id=str(uid_int), exit_strategy=kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP),
                    open_size_percent=100.0,
                    activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None
                )
                if rec_entity.status == RecommendationStatus.ACTIVE:
                    rec_entity.highest_price_reached = rec_entity.lowest_price_reached = rec_entity.entry.value

                created_rec = self.repo.add_with_event(session, rec_entity)
                final_rec, report = self.repo.publish_recommendation(session, created_rec.id, str(uid_int))
                session.commit()
                return final_rec, report
            except Exception as e:
                session.rollback()
                log.exception("Error during create_and_publish_recommendation.")
                raise e

    async def close_recommendation_for_user_async(self, rec_id: int, user_telegram_id: str, exit_price: float, reason: str = "MANUAL_CLOSE") -> Recommendation:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID.")

        with SessionLocal() as session:
            try:
                rec = self.repo.get_by_id_for_user(session, rec_id, uid_int)
                if not rec or rec.status == RecommendationStatus.CLOSED:
                    return rec or self.repo.get_by_id_for_user(session, rec_id, uid_int) # Return if not found or already closed
                
                rec.open_size_percent = 0.0
                rec.close(exit_price)
                pnl = _pct(rec.entry.value, exit_price, rec.side.value)
                close_status = "PROFIT" if pnl > 0.001 else "LOSS" if pnl < -0.001 else "BREAKEVEN"
                
                updated_rec = self.repo.update_with_event(session, rec, "CLOSED", {"exit_price": exit_price, "reason": reason, "close_status": close_status})
                await self._update_all_cards_async(session, updated_rec)
                
                emoji, r_text = ("🏆", "Profit") if close_status == "PROFIT" else ("💔", "Loss")
                self._notify_all_channels(session, rec_id, f"<b>{emoji} Trade Closed #{updated_rec.asset.value}</b>\nClosed at {exit_price:g} for a result of <b>{pnl:+.2f}%</b> ({r_text}).")
                
                session.commit()
                return updated_rec
            except Exception:
                session.rollback()
                raise

    async def activate_recommendation_async(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as session:
            try:
                rec = self.repo.get_for_update(session, rec_id)
                if not rec or rec.status != RecommendationStatus.PENDING:
                    return None
                
                rec.activate()
                rec.highest_price_reached = rec.lowest_price_reached = rec.entry.value
                updated_rec = self.repo.update_with_event(session, rec, "ACTIVATED", {})
                
                await self._update_all_cards_async(session, updated_rec)
                self._notify_all_channels(session, rec_id, f"▶️ **Trade Activated** | **{rec.asset.value}** entry price has been reached.")
                
                session.commit()
                return updated_rec
            except Exception:
                session.rollback()
                log.exception(f"Failed to activate recommendation #{rec_id}")
                raise

    def update_price_tracking(self, rec_id: int, current_price: float):
        with SessionLocal() as session:
            try:
                self.repo.update_price_tracking(session, rec_id, current_price)
                session.commit()
            except Exception:
                session.rollback()
                log.exception(f"Failed to update price tracking for rec #{rec_id}")

# --- END OF FINAL, RE-ARCHITECTED AND PRODUCTION-READY FILE ---