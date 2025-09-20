# --- START OF FINAL, REBUILT, AND SIGNATURE-CORRECTED FILE (Version 10.2.0) ---
# src/capitalguard/application/services/trade_service.py

import logging
import asyncio
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import NotifierPort
from capitalguard.infrastructure.db.repository import RecommendationRepository, ChannelRepository, UserRepository
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.models import PublishedMessage
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
    Core application service for managing trade recommendations.
    All methods now correctly accept a `Session` object, adhering to the Unit of Work pattern.
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

    # --- Private Helper Methods (remain unchanged) ---

    async def _update_all_cards_async(self, session: Session, rec: Recommendation):
        # ... (code is correct)
        published_messages = self.repo.get_published_messages(session, rec.id)
        if not published_messages:
            return
        log.info("Asynchronously updating %d cards for rec #%s...", len(published_messages), rec.id)
        update_tasks = [
            asyncio.to_thread(
                self.notifier.edit_recommendation_card_by_ids,
                channel_id=msg_meta.telegram_channel_id,
                message_id=msg_meta.telegram_message_id,
                rec=rec
            )
            for msg_meta in published_messages
        ]
        results = await asyncio.gather(*update_tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                msg_meta = published_messages[i]
                log.warning(f"Failed to update card for rec #{rec.id} in channel {msg_meta.telegram_channel_id}: {result}")

    def _notify_all_channels(self, session: Session, rec_id: int, text: str):
        # ... (code is correct)
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
        # ... (code is correct)
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

    def _publish_recommendation(self, session: Session, rec: Recommendation, user_id: str) -> Tuple[Recommendation, Dict]:
        # ... (code is correct)
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(session).find_by_telegram_id(uid_int)
        if not user:
            report["failed"].append({"reason": "User not found"})
            return rec, report
        channels = ChannelRepository(session).list_by_user(user.id, only_active=True)
        if not channels:
            report["failed"].append({"reason": "No active channels linked"})
            return rec, report
        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec.id, self.notifier.bot_username)
        for ch in channels:
            try:
                res = self.notifier.post_to_channel(ch.telegram_channel_id, rec, keyboard)
                if res:
                    publication = PublishedMessage(recommendation_id=rec.id, telegram_channel_id=res[0], telegram_message_id=res[1])
                    session.add(publication)
                    report["success"].append({"channel_id": ch.telegram_channel_id, "message_id": res[1]})
                else:
                    report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": "Notifier failed to post message."})
            except Exception as e:
                log.error(f"Failed to publish to channel {ch.telegram_channel_id}: {e}", exc_info=True)
                report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": str(e)})
        session.flush()
        return rec, report

    # --- Public Service Methods (Now with corrected signatures) ---
    
    def get_recommendation_for_user(self, session: Session, rec_id: int, user_telegram_id: str) -> Optional[Recommendation]:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID format.")
        return self.repo.get_by_id_for_user(session, rec_id, uid_int)
            
    def get_open_recommendations_for_user(self, session: Session, user_telegram_id: str, **filters) -> List[Recommendation]:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: return []
        return self.repo.list_open_for_user(session, uid_int, **filters)

    # ✅ FIX: Added 'session: Session' as the first argument.
    async def create_and_publish_recommendation_async(self, session: Session, **kwargs) -> Tuple[Recommendation, Dict]:
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
        final_rec, report = self._publish_recommendation(session, created_rec, str(uid_int))
        
        return final_rec, report

    # ... (The rest of the methods in TradeService were already correct)
    async def close_recommendation_for_user_async(self, session: Session, rec_id: int, user_telegram_id: str, exit_price: float, reason: str = "MANUAL_CLOSE") -> Recommendation:
        # ... (code is correct)
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID.")
        rec_orm = self.repo.get_for_update(session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != str(uid_int):
            raise ValueError(f"Recommendation #{rec_id} not found or access denied.")
        if rec.status == RecommendationStatus.CLOSED:
            return rec
        rec.open_size_percent = 0.0
        rec.close(exit_price)
        pnl = _pct(rec.entry.value, exit_price, rec.side.value)
        close_status = "PROFIT" if pnl > 0.001 else "LOSS" if pnl < -0.001 else "BREAKEVEN"
        updated_rec = self.repo.update_with_event(session, rec, "CLOSED", {"exit_price": exit_price, "reason": reason, "close_status": close_status})
        await self._update_all_cards_async(session, updated_rec)
        emoji, r_text = ("🏆", "Profit") if close_status == "PROFIT" else ("💔", "Loss")
        self._notify_all_channels(session, rec_id, f"<b>{emoji} Trade Closed #{updated_rec.asset.value}</b>\nClosed at {exit_price:g} for a result of <b>{pnl:+.2f}%</b> ({r_text}).")
        return updated_rec

    # ✅ FIX: Added 'session: Session' as the first argument.
    def get_recent_assets_for_user(self, session: Session, user_id: str, limit: int = 5) -> List[str]:
        uid_int = _parse_int_user_id(user_id)
        if not uid_int: return []
        return self.repo.get_recent_assets_for_user(session, user_telegram_id=uid_int, limit=limit)

# --- END OF FINAL, REBUILT, AND SIGNATURE-CORRECTED FILE ---