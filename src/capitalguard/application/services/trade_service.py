# --- START OF FINAL, PRODUCTION-READY FILE (Version 15.0.0) ---
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any, Set
from functools import wraps

from sqlalchemy.orm import Session
from telegram.error import BadRequest

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import NotifierPort
from capitalguard.infrastructure.db.repository import RecommendationRepository, ChannelRepository, UserRepository
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.price_service import PriceService
from capitalguard.infrastructure.db.models import PublishedMessage
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.interfaces.telegram.ui_texts import _pct

log = logging.getLogger(__name__)

# --- Helper Functions & Decorators ---

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """Safely parses a string user ID to an integer."""
    try:
        return int(user_id) if user_id is not None and user_id.strip().isdigit() else None
    except (TypeError, ValueError):
        return None

def uow_transaction(func):
    """
    Decorator to manage a database session for a single, atomic service method.
    It provides a session, commits on success, and rolls back on failure.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Check if a session is already passed (for nested calls within a transaction)
        if 'db_session' in kwargs:
            return await func(*args, **kwargs)
        
        with SessionLocal() as session:
            try:
                # Pass the session as a keyword argument to the decorated function
                result = await func(*args, db_session=session, **kwargs)
                session.commit()
                return result
            except Exception as e:
                log.error(f"Transaction failed in '{func.__name__}', rolling back. Error: {e}", exc_info=True)
                session.rollback()
                raise
    return wrapper

class TradeService:
    """
    Core application service for managing trade recommendations.
    ✅ FINAL ARCHITECTURE: Public methods that perform write operations are now decorated
    with @uow_transaction, making them atomic and isolated. The service manages its
    own sessions and no longer accepts them as parameters from callers like AlertService.
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

    # --- Private Helper Methods (Internal Logic) ---

    async def _update_all_cards_async(self, session: Session, rec: Recommendation):
        """Asynchronously updates all published Telegram messages for a recommendation."""
        published_messages = self.repo.get_published_messages(session, rec.id)
        if not published_messages:
            return

        log.info("Asynchronously updating %d cards for rec #%s...", len(published_messages), rec.id)
        
        for msg_meta in published_messages:
            try:
                await asyncio.to_thread(
                    self.notifier.edit_recommendation_card_by_ids,
                    channel_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    rec=rec
                )
            except BadRequest as e:
                if "message to edit not found" in str(e).lower():
                    log.warning(
                        f"Message {msg_meta.telegram_message_id} for rec #{rec.id} in channel "
                        f"{msg_meta.telegram_channel_id} was not found (likely deleted). "
                        f"Removing this publication record from the database."
                    )
                    session.delete(msg_meta)
                else:
                    log.error(f"A Telegram BadRequest occurred while updating card for rec #{rec.id}: {e}")
            except Exception as e:
                log.error(f"An unexpected error occurred while updating card for rec #{rec.id}: {e}", exc_info=True)

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
                log.warning(f"Failed to send reply notification for rec #{rec.id} to channel {msg_meta.telegram_channel_id}: {e}")

    def _validate_recommendation_data(self, side: str, entry: float, stop_loss: float, targets: List[Dict[str, float]]):
        """Centralized validation for core recommendation business rules."""
        side_upper = side.upper()
        
        if entry > 0:
            if side_upper == "LONG" and not (stop_loss < entry):
                raise ValueError("For new LONG trades, Stop Loss must be < Entry Price.")
            if side_upper == "SHORT" and not (stop_loss > entry):
                raise ValueError("For new SHORT trades, Stop Loss must be > Entry Price.")

        targets_vo = Targets(targets)
        for target in targets_vo.values:
            if entry > 0:
                if (side_upper == 'LONG' and target.price <= entry) or \
                   (side_upper == 'SHORT' and target.price >= entry):
                    raise ValueError(f"Target price {target.price} is not valid for a {side} trade with entry {entry}.")
            
            if (side_upper == 'LONG' and target.price <= stop_loss) or \
               (side_upper == 'SHORT' and target.price >= stop_loss):
                raise ValueError(f"Target price {target.price} cannot be on the same side of the trade as the stop loss {stop_loss}.")

    def _publish_recommendation(self, session: Session, rec: Recommendation, user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[Recommendation, Dict]:
        """Private helper to handle the logic of publishing a recommendation."""
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        uid_int = _parse_int_user_id(user_id)
        
        user = UserRepository(session).find_by_telegram_id(uid_int)
        if not user:
            report["failed"].append({"reason": "User not found"})
            return rec, report
        
        channels_to_publish = ChannelRepository(session).list_by_user(user.id, only_active=True)
        
        if target_channel_ids is not None:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]

        if not channels_to_publish:
            reason = "No active channels linked." if target_channel_ids is None else "No selected channels are active or linked."
            report["failed"].append({"reason": reason})
            return rec, report
        
        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec.id, self.notifier.bot_username)
        
        for ch in channels_to_publish:
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

    # --- Public Service Methods (Transactional) ---
    
    @uow_transaction
    async def create_and_publish_recommendation_async(self, *, db_session: Session, **kwargs) -> Tuple[Recommendation, Dict]:
        uid_int = _parse_int_user_id(kwargs.get('user_id'))
        if not uid_int: raise ValueError("A valid user_id is required.")

        target_channel_ids = kwargs.get('target_channel_ids')

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

        created_rec = self.repo.add_with_event(db_session, rec_entity)
        final_rec, report = self._publish_recommendation(db_session, created_rec, str(uid_int), target_channel_ids)
        
        return final_rec, report

    @uow_transaction
    async def close_recommendation_for_user_async(self, rec_id: int, user_telegram_id: str, exit_price: float, reason: str = "MANUAL_CLOSE", *, db_session: Session) -> Recommendation:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID.")

        rec_orm = self.repo.get_for_update(db_session, rec_id)
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
        
        updated_rec = self.repo.update_with_event(db_session, rec, "CLOSED", {"exit_price": exit_price, "reason": reason, "close_status": close_status})
        await self._update_all_cards_async(db_session, updated_rec)
        
        emoji, r_text = ("🏆", "Profit") if close_status == "PROFIT" else ("💔", "Loss")
        self._notify_all_channels(db_session, rec_id, f"<b>{emoji} Trade Closed #{updated_rec.asset.value}</b>\nClosed at {exit_price:g} for a result of <b>{pnl:+.2f}%</b> ({r_text}).")
        
        return updated_rec

    async def close_recommendation_at_market_for_user_async(self, rec_id: int, user_telegram_id: str) -> Recommendation:
        with SessionLocal() as session:
            rec = self.repo.get_by_id_for_user(session, rec_id, user_telegram_id)
        if not rec: raise ValueError(f"Recommendation #{rec_id} not found or access denied.")
        
        live_price = await self.price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
        if live_price is None: raise RuntimeError(f"Could not fetch live market price for {rec.asset.value}.")
        
        return await self.close_recommendation_for_user_async(rec_id, user_telegram_id, live_price, reason="MANUAL_MARKET_CLOSE")

    @uow_transaction
    async def activate_recommendation_async(self, rec_id: int, *, db_session: Session) -> Optional[Recommendation]:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: return None
        
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.status != RecommendationStatus.PENDING:
            return rec
        
        rec.activate()
        rec.highest_price_reached = rec.lowest_price_reached = rec.entry.value
        updated_rec = self.repo.update_with_event(db_session, rec, "ACTIVATED", {})
        
        await self._update_all_cards_async(db_session, updated_rec)
        self._notify_all_channels(db_session, rec_id, f"▶️ **Trade Activated** | **{rec.asset.value}** entry price has been reached.")
        
        return updated_rec

    @uow_transaction
    async def take_partial_profit_for_user_async(self, rec_id: int, user_id: str, close_percent: float, price: float, triggered_by: str = "MANUAL", *, db_session: Session) -> Recommendation:
        uid_int = _parse_int_user_id(user_id)
        if not uid_int: raise ValueError("Invalid User ID.")

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)

        if not rec or rec.user_id != user_id: raise ValueError("Access denied.")
        if rec.status != RecommendationStatus.ACTIVE: raise ValueError("Partial profit can only be taken on active recommendations.")
        if not (0 < close_percent <= rec.open_size_percent): raise ValueError(f"Invalid percentage. Must be between 0 and {rec.open_size_percent:.2f}.")

        rec.open_size_percent -= close_percent
        pnl_on_part = _pct(rec.entry.value, price, rec.side.value)
        event_type = "PARTIAL_PROFIT_AUTO" if triggered_by.upper() == "AUTO" else "PARTIAL_PROFIT_MANUAL"
        event_data = {"price": price, "closed_percent": close_percent, "remaining_percent": rec.open_size_percent, "pnl_on_part": pnl_on_part, "triggered_by": triggered_by}
        
        updated_rec = self.repo.update_with_event(db_session, rec, event_type, event_data)
        
        notification_text = (f"💰 **Partial Profit Taken** | Signal #{rec.id}\n\n"
                           f"Closed **{close_percent:.2f}%** of **{rec.asset.value}** at **{price:g}** for a **{pnl_on_part:+.2f}%** profit.\n\n"
                           f"<i>Remaining open size: {rec.open_size_percent:.2f}%</i>")
        self._notify_all_channels(db_session, rec_id, notification_text)
        await self._update_all_cards_async(db_session, updated_rec)
        
        if updated_rec.open_size_percent <= 0.01:
            log.info(f"Recommendation #{rec_id} fully closed via partial profits. Marking as closed.")
            reason = "AUTO_PARTIAL_FULL_CLOSE" if triggered_by.upper() == "AUTO" else "MANUAL_PARTIAL_FULL_CLOSE"
            return await self.close_recommendation_for_user_async(rec_id, user_id, price, reason=reason, db_session=db_session)

        return updated_rec

    @uow_transaction
    async def process_target_hit_async(self, rec_id: int, user_id: str, target_index: int, hit_price: float, *, db_session: Session):
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)

        if not rec_orm or rec_orm.status != 'ACTIVE' or not rec:
            return
        
        if not rec.targets.values or len(rec.targets.values) < target_index:
            return

        target = rec.targets.values[target_index - 1]
        
        event_type = f"TP{target_index}_HIT"
        updated_rec = self.repo.update_with_event(db_session, rec, event_type, {"price": hit_price, "target": target.price})
        
        note = f"🔥 **Target {target_index} Hit!** | **{rec.asset.value}** reached **{target.price:g}**."
        self._notify_all_channels(db_session, rec_id, note)
        
        if target.close_percent > 0:
            log.info(f"Auto partial profit triggered for rec #{rec_id} at TP{target_index}.")
            await self.take_partial_profit_for_user_async(rec.id, user_id, target.close_percent, target.price, triggered_by="AUTO", db_session=db_session)
        
        await self._update_all_cards_async(db_session, updated_rec)

    @uow_transaction
    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: float, *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access Denied.")
        if rec.status == RecommendationStatus.CLOSED: raise ValueError("Cannot update SL for a closed recommendation.")

        old_sl = rec.stop_loss.value
        rec.stop_loss = Price(new_sl)
        updated_rec = self.repo.update_with_event(db_session, rec, "SL_UPDATED", {"old_sl": old_sl, "new_sl": new_sl})
        await self._update_all_cards_async(db_session, updated_rec)
        self._notify_all_channels(db_session, rec_id, f"✏️ **Stop Loss Updated** for #{rec.asset.value} to **{new_sl:g}**.")
        return updated_rec

    @uow_transaction
    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, float]], *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access Denied.")
        if rec.status == RecommendationStatus.CLOSED: raise ValueError("Cannot update targets for a closed recommendation.")

        old_targets = [t.price for t in rec.targets.values]
        rec.targets = Targets(new_targets)
        updated_rec = self.repo.update_with_event(db_session, rec, "TARGETS_UPDATED", {"old": old_targets, "new": [t.price for t in rec.targets.values]})
        await self._update_all_cards_async(db_session, updated_rec)
        self._notify_all_channels(db_session, rec_id, f"🎯 **Targets Updated** for #{rec.asset.value}.")
        return updated_rec

    @uow_transaction
    async def update_exit_strategy_for_user_async(self, rec_id: int, user_id: str, new_strategy: ExitStrategy, *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access Denied.")
        if rec.status == RecommendationStatus.CLOSED: return rec

        old_strategy = rec.exit_strategy
        rec.exit_strategy = new_strategy
        updated_rec = self.repo.update_with_event(db_session, rec, "STRATEGY_UPDATED", {"old": old_strategy.value, "new": new_strategy.value})
        await self._update_all_cards_async(db_session, updated_rec)
        self._notify_all_channels(db_session, rec_id, f"📈 **Exit Strategy Updated** for #{rec.asset.value}.")
        return updated_rec
        
    @uow_transaction
    async def update_profit_stop_for_user_async(self, rec_id: int, user_id: str, new_price: Optional[float], *, db_session: Session) -> Recommendation:
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        rec = self.repo._to_entity(rec_orm)
        if not rec or rec.user_id != user_id: raise ValueError("Access Denied.")
        if rec.status != RecommendationStatus.ACTIVE: raise ValueError("Profit Stop can only be set on active recommendations.")
        
        old_price = rec.profit_stop_price
        rec.profit_stop_price = new_price
        updated_rec = self.repo.update_with_event(db_session, rec, "PROFIT_STOP_UPDATED", {"old": old_price, "new": new_price})
        await self._update_all_cards_async(db_session, updated_rec)

        if new_price is not None:
            note = f"🛡️ **Profit Stop Set** for #{rec.asset.value} at **{new_price:g}**."
        else:
            note = f"🗑️ **Profit Stop Removed** for #{rec.asset.value}."
        self._notify_all_channels(db_session, rec_id, note)
        
        return updated_rec

    # --- Read-only methods do not need the decorator ---
    def get_recommendation_for_user(self, session: Session, rec_id: int, user_telegram_id: str) -> Optional[Recommendation]:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: raise ValueError("Invalid User ID format.")
        return self.repo.get_by_id_for_user(session, rec_id, uid_int)
            
    def get_open_recommendations_for_user(self, session: Session, user_telegram_id: str, **filters) -> List[Recommendation]:
        uid_int = _parse_int_user_id(user_telegram_id)
        if not uid_int: return []
        return self.repo.list_open_for_user(session, uid_int, **filters)

    def get_recent_assets_for_user(self, session: Session, user_id: str, limit: int = 5) -> List[str]:
        uid_int = _parse_int_user_id(user_id)
        if not uid_int: return []
        return self.repo.get_recent_assets_for_user(session, user_telegram_id=uid_int, limit=limit)

# --- END OF FINAL, PRODUCTION-READY FILE (Version 15.0.0) ---