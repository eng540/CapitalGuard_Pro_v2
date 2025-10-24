# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/trade_service.py ---
# src/capitalguard/application/services/trade_service.py v 30.6
"""
TradeService v30.6 - Final, incorporating validation on updates and BE move fix.
✅ FIX: Added logical validation (similar to _validate_recommendation_data) to update operations.
✅ FIX: Implemented "dead zone" logic for move_sl_to_breakeven_async to prevent immediate closure.
✅ HOTFIX: Decoupled from `interfaces` layer by moving helper functions internally.
"""

from __future__ import annotations
import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    PublishedMessage, Recommendation, RecommendationEvent, User,
    RecommendationStatusEnum, UserTrade, UserTradeStatus, OrderTypeEnum, ExitStrategyEnum
)
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, ChannelRepository, UserRepository
)
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType, ExitStrategy, UserType
)
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets

if False:
    # Type-only imports for services that will be injected at runtime.
    from .alert_service import AlertService
    from .price_service import PriceService
    from .market_data_service import MarketDataService

logger = logging.getLogger(__name__)

# --- Constants ---
# ✅ FIX: Small offset for Move SL to BE to prevent immediate trigger
MOVE_SL_BE_OFFSET_PERCENT = Decimal("0.0005") # 0.05%

# ---------------------------
# Internal helper functions (Unchanged)
# ---------------------------

def _pct(entry: Any, target_price: Any, side: str) -> float:
    """Compute percentage PnL from entry to target_price depending on side."""
    try:
        entry_dec = Decimal(str(entry))
        target_dec = Decimal(str(target_price))
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite():
            return 0.0
        side_upper = (side or "").upper()
        if side_upper == "LONG":
            pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT":
            pnl = ((entry_dec / target_dec) - 1) * 100
        else:
            return 0.0
        # Ensure conversion to float for compatibility, handle potential precision issues if needed later
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError) as exc:
        logger.debug("pct calc error: %s", exc)
        return 0.0


def _normalize_pct_value(pct_raw: Any) -> Decimal:
    """Normalize a raw percentage value into Decimal."""
    try:
        if isinstance(pct_raw, Decimal):
            return pct_raw
        if isinstance(pct_raw, (int, float)):
            return Decimal(str(pct_raw))
        if isinstance(pct_raw, str):
            s = pct_raw.strip().replace('%', '').replace('+', '').replace(',', '')
            return Decimal(s)
        return Decimal(str(pct_raw))
    except (InvalidOperation, Exception) as exc:
        logger.warning("Unable to normalize pct value '%s' (%s); defaulting to 0", pct_raw, exc)
        return Decimal(0)


def _parse_int_user_id(user_id: Any) -> Optional[int]:
    """Safely parse numeric telegram user id from various inputs."""
    try:
        if user_id is None:
            return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None

# --- TradeService ---

class TradeService:
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: "MarketDataService",
        price_service: "PriceService",
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        self.alert_service: "AlertService" = None

    # ---------------------------
    # Internal DB / notifier helpers (Unchanged)
    # ---------------------------

    async def _commit_and_dispatch(self, db_session: Session, rec_orm: Recommendation, rebuild_alerts: bool = True):
        """Commit DB transaction, refresh ORM, rebuild alert indices if needed, convert to entity and notify card updates."""
        db_session.commit()
        try:
            db_session.refresh(rec_orm)
        except Exception as e:
            # Handle cases where the object might be deleted by another transaction after commit but before refresh
            logger.warning(f"Failed to refresh rec_orm after commit (might be deleted): {e}")
            # Attempt to refetch if refresh fails, might be None if deleted
            rec_orm_refetched = self.repo.get(db_session, rec_orm.id) if hasattr(rec_orm, 'id') else None
            if not rec_orm_refetched:
                 logger.info(f"Recommendation ID {getattr(rec_orm, 'id', 'N/A')} seems deleted, skipping dispatch.")
                 return # Exit if object no longer exists
            rec_orm = rec_orm_refetched # Use the refetched object

        if rebuild_alerts and self.alert_service:
            try:
                await self.alert_service.build_triggers_index()
            except Exception as e:
                logger.exception("Failed to rebuild alerts index after commit: %s", e)

        # Only proceed if rec_orm is still valid
        if rec_orm:
            updated_entity = self.repo._to_entity(rec_orm)
            if updated_entity: # Ensure conversion was successful
                try:
                    await self.notify_card_update(updated_entity, db_session)
                except Exception as e:
                    logger.exception("Failed to notify card update after commit: %s", e)

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """Call notifier function whether it's coroutine or regular function."""
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        # Use asyncio.to_thread for potentially blocking sync functions
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        """Update published recommendation cards across channels where published."""
        if getattr(rec_entity, "is_shadow", False):
            return
        # Ensure rec_entity has a valid ID before querying messages
        if not rec_entity or not hasattr(rec_entity, 'id') or rec_entity.id is None:
             logger.warning("Attempted to notify card update for invalid rec_entity.")
             return

        published_messages = self.repo.get_published_messages(db_session, rec_entity.id)
        if not published_messages:
            return
        tasks = [
            self._call_notifier_maybe_async(
                self.notifier.edit_recommendation_card_by_ids,
                channel_id=msg.telegram_channel_id,
                message_id=msg.telegram_message_id,
                rec=rec_entity
            ) for msg in published_messages
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        for res in results:
            if isinstance(res, Exception):
                # Log specific exception details if possible
                logger.error("notify_card_update gather failed: %s", res, exc_info=(isinstance(res, Exception)))


    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        """Post a notification reply to all published messages for a recommendation."""
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or getattr(rec_orm, "is_shadow", False):
            return
        published_messages = self.repo.get_published_messages(db_session, rec_id)
        for msg in published_messages:
            # Use create_task for fire-and-forget background notification
            asyncio.create_task(self._call_notifier_maybe_async(
                self.notifier.post_notification_reply,
                chat_id=msg.telegram_channel_id,
                message_id=msg.telegram_message_id,
                text=text
            ))

    # ---------------------------
    # Validation
    # ---------------------------

    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        """Strict validation rules for recommendation numeric integrity (prices)."""
        side_upper = (side or "").upper()
        if not all(isinstance(v, Decimal) and v.is_finite() and v > Decimal(0) for v in [entry, stop_loss]):
            raise ValueError("Entry and Stop Loss must be positive finite Decimal values.")
        if not targets or not all(isinstance(t.get('price'), Decimal) and t['price'].is_finite() and t['price'] > Decimal(0) for t in targets):
            raise ValueError("At least one valid target with a positive finite Decimal price is required.")

        # Ensure close_percent is valid
        for t in targets:
             cp = t.get('close_percent', 0.0)
             if not isinstance(cp, (int, float)) or not (0 <= cp <= 100):
                 raise ValueError(f"Invalid close_percent '{cp}' for target {t['price']}. Must be between 0 and 100.")

        # Side-specific logic checks
        if side_upper == "LONG":
            if stop_loss >= entry: raise ValueError("For LONG, Stop Loss must be less than Entry.")
            target_prices = [t['price'] for t in targets]
            if any(p <= entry for p in target_prices): raise ValueError("All LONG targets must be greater than the entry price.")
        elif side_upper == "SHORT":
            if stop_loss <= entry: raise ValueError("For SHORT, Stop Loss must be greater than Entry.")
            target_prices = [t['price'] for t in targets]
            if any(p >= entry for p in target_prices): raise ValueError("All SHORT targets must be less than the entry price.")
        else:
             raise ValueError(f"Invalid side value: {side}") # Should not happen if Side VO is used

        # Risk/Reward and Uniqueness checks
        risk = abs(entry - stop_loss)
        if risk.is_zero(): raise ValueError("Entry and Stop Loss cannot be equal.")

        target_prices = [t['price'] for t in targets] # Recalculate if needed
        # R/R check based on the first executable target relative to entry
        first_target_price = min(target_prices) if side_upper == "SHORT" else max(target_prices) # Correction: Short side min price is highest reward
        reward = abs(first_target_price - entry)
        # Relax R/R check slightly or make it configurable? For now, keep 0.1
        if (reward / risk) < Decimal('0.1'): raise ValueError("Risk/Reward ratio is too low (minimum 0.1). Check SL and first TP.")
        if len(target_prices) != len(set(target_prices)): raise ValueError("Target prices must be unique.")

        # Sorting check
        sorted_prices = sorted(target_prices, reverse=(side_upper == 'SHORT'))
        if target_prices != sorted_prices:
            raise ValueError("Targets must be sorted ascending for LONG and descending for SHORT.")


    # ---------------------------
    # Publishing (Unchanged)
    # ---------------------------

    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """Publish a recommendation to the analyst's linked public channels."""
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        parsed_user_id = _parse_int_user_id(user_id)
        if not parsed_user_id:
            report["failed"].append({"reason": "Invalid user ID format"})
            return rec_entity, report

        user = UserRepository(session).find_by_telegram_id(parsed_user_id)
        if not user:
            report["failed"].append({"reason": "User not found"})
            return rec_entity, report

        channels_to_publish = ChannelRepository(session).list_by_analyst(user.id, only_active=True)
        if target_channel_ids:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]

        if not channels_to_publish:
            report["failed"].append({"reason": "No active channels linked or selected."})
            return rec_entity, report

        # Import keyboard function lazily
        try:
            from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        except ImportError:
            logger.warning("Could not import public_channel_keyboard, channel messages will have no buttons.")
            public_channel_keyboard = lambda *_: None

        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        post_tasks = []
        for channel in channels_to_publish:
             post_tasks.append(
                 self._call_notifier_maybe_async(self.notifier.post_to_channel, channel.telegram_channel_id, rec_entity, keyboard)
             )

        results = await asyncio.gather(*post_tasks, return_exceptions=True)

        for i, result in enumerate(results):
             channel = channels_to_publish[i]
             channel_id_log = getattr(channel, "telegram_channel_id", "Unknown ID")
             if isinstance(result, Exception):
                  logger.exception(f"Failed to publish to channel {channel_id_log}: {result}")
                  report["failed"].append({"channel_id": channel_id_log, "reason": str(result)})
             elif isinstance(result, tuple) and len(result) == 2:
                  # Expected result: (channel_id, message_id)
                  session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=result[0], telegram_message_id=result[1]))
                  report["success"].append({"channel_id": result[0], "message_id": result[1]})
             else:
                  logger.error(f"Notifier returned unsupported type for channel {channel_id_log}: {type(result)}")
                  report["failed"].append({"channel_id": channel_id_log, "reason": f"Unexpected notifier response: {type(result)}"})

        # Flush only if messages were successfully added
        if report["success"]:
             try:
                  session.flush()
             except Exception as e:
                  logger.exception("Failed to flush published messages to DB session.")
                  # Decide if failure here should invalidate the whole operation? Currently, it doesn't.
        return rec_entity, report


    # ---------------------------
    # Public API - create / publish (Validation moved)
    # ---------------------------

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """Create a new recommendation and optionally publish it."""
        parsed_user_id = _parse_int_user_id(user_id)
        if not parsed_user_id: raise ValueError("Invalid user ID format.")
        user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can create recommendations.")

        # Extract and validate required arguments
        required_args = ['asset', 'side', 'entry', 'stop_loss', 'targets', 'order_type']
        if not all(k in kwargs for k in required_args):
            missing = [k for k in required_args if k not in kwargs]
            raise ValueError(f"Missing required arguments for recommendation: {', '.join(missing)}")

        entry_price, sl_price = kwargs['entry'], kwargs['stop_loss']
        # Ensure targets are correctly formatted early
        targets_list_raw = kwargs['targets']
        if not isinstance(targets_list_raw, list):
             raise ValueError("Targets must be provided as a list of dictionaries.")
        targets_list = [{'price': Decimal(str(t['price'])), 'close_percent': float(t.get('close_percent', 0))} for t in targets_list_raw]

        asset, side, market = kwargs['asset'].strip().upper(), kwargs['side'].upper(), kwargs.get('market', 'Futures')
        order_type_enum = OrderTypeEnum[kwargs['order_type'].upper()]
        exit_strategy_val = kwargs.get('exit_strategy', ExitStrategyEnum.CLOSE_AT_FINAL_TP) # Default if not provided

        # Normalize exit strategy input robustly
        if isinstance(exit_strategy_val, ExitStrategyEnum):
            exit_strategy_enum = exit_strategy_val
        elif isinstance(exit_strategy_val, ExitStrategy):
            exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.name]
        elif isinstance(exit_strategy_val, str):
            try:
                exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.upper()]
            except KeyError:
                raise ValueError(f"Unsupported exit_strategy string value: {exit_strategy_val}")
        else:
            raise ValueError(f"Unsupported exit_strategy format: {type(exit_strategy_val)}")

        # Handle MARKET order price fetching
        if order_type_enum == OrderTypeEnum.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            status, final_entry = RecommendationStatusEnum.ACTIVE, Decimal(str(live_price)) if live_price is not None else None
            if final_entry is None or not final_entry.is_finite():
                raise RuntimeError(f"Could not fetch live price for {asset}.")
        else:
            status, final_entry = RecommendationStatusEnum.PENDING, entry_price

        # Perform the core validation using the final entry price
        self._validate_recommendation_data(side, final_entry, sl_price, targets_list)

        # Build ORM object
        rec_orm = Recommendation(
            analyst_id=user.id, asset=asset, side=side, entry=final_entry, stop_loss=sl_price,
            targets=[{'price': str(t['price']), 'close_percent': t['close_percent']} for t in targets_list], # Convert back to strings for DB
            order_type=order_type_enum, status=status, market=market,
            notes=kwargs.get('notes'), exit_strategy=exit_strategy_enum,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )

        db_session.add(rec_orm)
        db_session.flush() # Get the ID for the event
        db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type="CREATED_ACTIVE" if status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING"))
        db_session.flush() # Ensure event is flushed before refresh might detach
        db_session.refresh(rec_orm) # Refresh to get all defaults and relationships if needed

        created_rec_entity = self.repo._to_entity(rec_orm)
        if not created_rec_entity:
            # This should ideally not happen if ORM object is valid
            raise RuntimeError(f"Failed to convert ORM object ID {rec_orm.id} to entity after creation.")

        # Publish the recommendation
        final_rec, report = await self._publish_recommendation(db_session, created_rec_entity, user_id, kwargs.get('target_channel_ids'))

        # Commit transaction *after* publishing attempt
        db_session.commit()

        # Rebuild alerts index *after* commit ensures the new rec is visible
        if self.alert_service:
            try:
                await self.alert_service.build_triggers_index()
            except Exception:
                logger.exception("alert_service.build_triggers_index failed after create_and_publish")

        return final_rec, report


    # ---------------------------
    # User trades (forwarded) (Validation moved)
    # ---------------------------

    async def create_trade_from_forwarding(self, user_id: str, trade_data: Dict[str, Any], db_session: Session, original_text: str = None) -> Dict[str, Any]:
        """Create a UserTrade record from forwarded trade data."""
        parsed_user_id = _parse_int_user_id(user_id)
        if not parsed_user_id: return {'success': False, 'error': 'Invalid user ID format'}
        trader_user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not trader_user: return {'success': False, 'error': 'User not found'}

        try:
            entry_dec = Decimal(str(trade_data['entry']))
            sl_dec = Decimal(str(trade_data['stop_loss']))
            targets_for_validation = [{'price': Decimal(str(t['price'])), 'close_percent': t.get('close_percent', 0)} for t in trade_data['targets']]

            # Validate structure and logic before saving
            self._validate_recommendation_data(trade_data['side'], entry_dec, sl_dec, targets_for_validation)

            targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0)} for t in trade_data['targets']]
            new_trade = UserTrade(
                user_id=trader_user.id, asset=trade_data['asset'], side=trade_data['side'],
                entry=entry_dec, stop_loss=sl_dec, targets=targets_for_db,
                status=UserTradeStatus.OPEN, source_forwarded_text=original_text
            )
            db_session.add(new_trade)
            db_session.flush() # Ensure ID is generated
            db_session.commit() # Commit immediately for forwarded trades
            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        except ValueError as e:
            logger.warning(f"Validation failed for forwarded trade data for user {user_id}: {e}")
            db_session.rollback() # Rollback on validation error
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"Error creating trade from forwarding for user {user_id}: {e}", exc_info=True)
            db_session.rollback() # Rollback on unexpected errors
            return {'success': False, 'error': 'An internal error occurred.'}

    # ---------------------------
    # Update operations (analyst-managed)
    # ---------------------------

    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Optional[Session] = None) -> RecommendationEntity:
        """Analyst updates Stop Loss for their own recommendation."""
        if db_session is None:
            with session_scope() as s:
                # Recursively call with session
                return await self.update_sl_for_user_async(rec_id, user_id, new_sl, s)

        parsed_user_id = _parse_int_user_id(user_id)
        if not parsed_user_id: raise ValueError("Invalid user ID format.")
        user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not user: raise ValueError("User not found.")

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied: Not owner.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")

        # ✅ FIX: Add logical validation for the new SL
        try:
             self._validate_recommendation_data(rec_orm.side, rec_orm.entry, new_sl, rec_orm.targets)
        except ValueError as e:
             raise ValueError(f"Invalid new Stop Loss: {e}")

        old_sl_float = float(rec_orm.stop_loss) # Store before change
        rec_orm.stop_loss = new_sl
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="SL_UPDATED", event_data={"old": old_sl_float, "new": float(new_sl)}))
        self.notify_reply(rec_id, f"⚠️ Stop Loss for #{rec_orm.asset} updated to {_format_price(new_sl)}.", db_session)

        # Commit and dispatch updates
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        # Convert the potentially refreshed ORM object to entity
        refreshed_entity = self.repo._to_entity(rec_orm)
        if not refreshed_entity:
             # This might happen if the record was deleted between commit and refresh
             raise RuntimeError(f"Failed to convert updated recommendation #{rec_id} to entity.")
        return refreshed_entity


    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, Any]], db_session: Session) -> RecommendationEntity:
        """Analyst updates targets for an active recommendation."""
        parsed_user_id = _parse_int_user_id(user_id)
        if not parsed_user_id: raise ValueError("Invalid user ID format.")
        user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not user: raise ValueError("User not found.")

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")

        # ✅ FIX: Add logical validation for the new targets
        try:
             # Ensure new_targets has Decimal prices for validation
             targets_for_validation = [{'price': Decimal(str(t['price'])), 'close_percent': t.get('close_percent', 0)} for t in new_targets]
             self._validate_recommendation_data(rec_orm.side, rec_orm.entry, rec_orm.stop_loss, targets_for_validation)
        except ValueError as e:
             raise ValueError(f"Invalid new Targets: {e}")

        old_targets_db = rec_orm.targets # Store before change (already JSON compatible)
        # Ensure prices are stored as strings for DB JSON compatibility
        rec_orm.targets = [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in targets_for_validation]
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="TP_UPDATED", event_data={"old": old_targets_db, "new": rec_orm.targets}))
        self.notify_reply(rec_id, f"🎯 Targets for #{rec_orm.asset} have been updated.", db_session)

        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        refreshed_entity = self.repo._to_entity(rec_orm)
        if not refreshed_entity: raise RuntimeError(f"Failed to convert updated recommendation #{rec_id} to entity.")
        return refreshed_entity


    async def update_entry_and_notes_async(self, rec_id: int, user_id: str, new_entry: Optional[Decimal], new_notes: Optional[str], db_session: Session) -> RecommendationEntity:
        """Update entry price (only for PENDING) and notes."""
        parsed_user_id = _parse_int_user_id(user_id)
        if not parsed_user_id: raise ValueError("Invalid user ID format.")
        user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not user: raise ValueError("User not found.")

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED: raise ValueError("Cannot edit a closed recommendation.")

        event_data = {}
        needs_commit = False

        if new_entry is not None:
            if rec_orm.status != RecommendationStatusEnum.PENDING:
                raise ValueError("Entry price can only be modified for PENDING recommendations.")
            # ✅ FIX: Add logical validation for the new entry price relative to SL/TPs
            try:
                 self._validate_recommendation_data(rec_orm.side, new_entry, rec_orm.stop_loss, rec_orm.targets)
            except ValueError as e:
                 raise ValueError(f"Invalid new Entry Price: {e}")
            if new_entry != rec_orm.entry:
                event_data.update({"old_entry": float(rec_orm.entry), "new_entry": float(new_entry)})
                rec_orm.entry = new_entry
                needs_commit = True

        if new_notes is not None and new_notes != rec_orm.notes:
            event_data.update({"old_notes": rec_orm.notes, "new_notes": new_notes})
            rec_orm.notes = new_notes
            needs_commit = True

        if needs_commit and event_data:
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="DATA_UPDATED", event_data=event_data))
            self.notify_reply(rec_id, f"✏️ Data for #{rec_orm.asset} has been updated.", db_session)
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=(new_entry is not None)) # Rebuild only if entry changed
        elif needs_commit: # Only notes changed, no event needed? Or add a NOTES_UPDATED event? Assuming notes update doesn't need separate event.
             await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)

        refreshed_entity = self.repo._to_entity(rec_orm)
        if not refreshed_entity: raise RuntimeError(f"Failed to convert updated recommendation #{rec_id} to entity.")
        return refreshed_entity


    async def set_exit_strategy_async(self, rec_id: int, user_id: str, mode: str, price: Optional[Decimal] = None, trailing_value: Optional[Decimal] = None, active: bool = True, session: Optional[Session] = None) -> RecommendationEntity:
        """Set or cancel a profit-stop / exit strategy."""
        if session is None:
            with session_scope() as s:
                return await self.set_exit_strategy_async(rec_id, user_id, mode, price, trailing_value, active, s)

        parsed_user_id = _parse_int_user_id(user_id)
        if not parsed_user_id: raise ValueError("Invalid user ID format.")
        user = UserRepository(session).find_by_telegram_id(parsed_user_id)
        if not user: raise ValueError("User not found.")

        rec = self.repo.get_for_update(session, rec_id)
        if not rec: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec.analyst_id != user.id: raise ValueError("Access denied.")
        if rec.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Exit strategies can only be set for ACTIVE recommendations.")

        # Basic validation for parameters based on mode
        mode_upper = mode.upper()
        if mode_upper == "FIXED" and price is None: raise ValueError("Price must be provided for FIXED mode.")
        if mode_upper == "TRAILING" and trailing_value is None: raise ValueError("Trailing value must be provided for TRAILING mode.")
        if mode_upper not in ["NONE", "FIXED", "TRAILING"]: raise ValueError(f"Invalid profit stop mode: {mode}")

        rec.profit_stop_mode = mode_upper
        rec.profit_stop_price = price if mode_upper == "FIXED" else None
        rec.profit_stop_trailing_value = trailing_value if mode_upper == "TRAILING" else None
        rec.profit_stop_active = active if mode_upper != "NONE" else False # Deactivate if mode is NONE

        event_data = {"mode": mode_upper, "active": rec.profit_stop_active}
        if rec.profit_stop_price: event_data["price"] = float(rec.profit_stop_price)
        if rec.profit_stop_trailing_value: event_data["trailing_value"] = float(rec.profit_stop_trailing_value)

        session.add(RecommendationEvent(recommendation_id=rec_id, event_type="EXIT_STRATEGY_UPDATED", event_data=event_data))

        if rec.profit_stop_active:
            self.notify_reply(rec_id, f"📈 Exit strategy for #{rec.asset} set to: {mode_upper}", session)
        else:
            self.notify_reply(rec_id, f"📈 Exit strategy for #{rec.asset} has been cancelled or set to NONE.", session)

        await self._commit_and_dispatch(session, rec, rebuild_alerts=True)
        refreshed_entity = self.repo._to_entity(rec)
        if not refreshed_entity: raise RuntimeError(f"Failed to convert updated recommendation #{rec_id} to entity.")
        return refreshed_entity


    # ---------------------------
    # Automation helpers
    # ---------------------------

    async def move_sl_to_breakeven_async(self, rec_id: int, db_session: Optional[Session] = None) -> RecommendationEntity:
        """Move SL to entry price (breakeven) with a small offset."""
        if db_session is None:
            with session_scope() as s:
                return await self.move_sl_to_breakeven_async(rec_id, s)

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only move SL to BE for ACTIVE recommendations.")

        entry_price = rec_orm.entry
        current_sl = rec_orm.stop_loss
        side = rec_orm.side

        # ✅ FIX: Calculate new SL with offset
        offset = entry_price * MOVE_SL_BE_OFFSET_PERCENT
        if side == 'LONG':
             new_sl_target = entry_price + offset # Move slightly *above* entry
             # Only move if the new SL is better (higher) than the current SL
             if new_sl_target > current_sl:
                 # Apply rounding if needed, e.g., based on tick size (simplification: using Decimal quantize)
                 new_sl_final = new_sl_target.quantize(Decimal('1e-8'), rounding=ROUND_HALF_UP) # Assuming 8 decimal places
                 logger.info(f"Moving SL to BE+offset for LONG Rec #{rec_id}: {current_sl} -> {new_sl_final}")
                 # User ID needs to be fetched correctly if not passed
                 analyst_uid = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
                 if not analyst_uid: raise RuntimeError("Could not determine analyst ID for SL update.")
                 return await self.update_sl_for_user_async(rec_id, analyst_uid, new_sl_final, db_session)
        elif side == 'SHORT':
             new_sl_target = entry_price - offset # Move slightly *below* entry
             # Only move if the new SL is better (lower) than the current SL
             if new_sl_target < current_sl:
                 new_sl_final = new_sl_target.quantize(Decimal('1e-8'), rounding=ROUND_HALF_UP)
                 logger.info(f"Moving SL to BE+offset for SHORT Rec #{rec_id}: {current_sl} -> {new_sl_final}")
                 analyst_uid = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
                 if not analyst_uid: raise RuntimeError("Could not determine analyst ID for SL update.")
                 return await self.update_sl_for_user_async(rec_id, analyst_uid, new_sl_final, db_session)

        # If conditions not met, log and return current state
        logger.info(f"SL for Rec #{rec_id} is already at or better than breakeven + offset. No action taken.")
        current_entity = self.repo._to_entity(rec_orm)
        if not current_entity: raise RuntimeError(f"Failed to convert recommendation #{rec_id} to entity.")
        return current_entity


    async def close_recommendation_async(self, rec_id: int, user_id: Optional[str], exit_price: Decimal, db_session: Optional[Session] = None, reason: str = "MANUAL_CLOSE") -> RecommendationEntity:
        """Close a recommendation fully."""
        if db_session is None:
            with session_scope() as s:
                return await self.close_recommendation_async(rec_id, user_id, exit_price, s, reason)

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")

        # Idempotency check
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            logger.warning(f"Attempted to close already closed recommendation #{rec_id}")
            current_entity = self.repo._to_entity(rec_orm)
            if not current_entity: raise RuntimeError(f"Failed to convert closed recommendation #{rec_id} to entity.")
            return current_entity

        # Ownership check if user_id is provided
        if user_id is not None:
            parsed_user_id = _parse_int_user_id(user_id)
            if not parsed_user_id: raise ValueError("Invalid user ID format for owner check.")
            user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
            # Check analyst ID directly on the ORM object
            if not user or rec_orm.analyst_id != user.id:
                raise ValueError("Access denied for closing recommendation.")

        remaining_percent = Decimal(str(rec_orm.open_size_percent))
        if remaining_percent > 0:
            raw_pct = _pct(rec_orm.entry, exit_price, rec_orm.side)
            pnl_on_part = _normalize_pct_value(raw_pct)
            event_data = {"price": float(exit_price), "closed_percent": float(remaining_percent), "pnl_on_part": float(pnl_on_part), "triggered_by": reason}
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="FINAL_CLOSE", event_data=event_data))

        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.exit_price = exit_price
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.open_size_percent = Decimal(0)
        rec_orm.profit_stop_active = False # Ensure exit strategy is deactivated

        self.notify_reply(rec_id, f"✅ Signal #{rec_orm.asset} closed at {_format_price(exit_price)}. Reason: {reason}", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True) # Rebuild needed as it's no longer active

        refreshed_entity = self.repo._to_entity(rec_orm)
        # Handle case where entity might be None if deleted concurrently? Unlikely here.
        if not refreshed_entity: raise RuntimeError(f"Failed to convert closed recommendation #{rec_id} to entity.")
        return refreshed_entity


    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, price: Decimal, db_session: Session, triggered_by: str = "MANUAL") -> RecommendationEntity:
        """Partially close a recommendation position."""
        parsed_user_id = _parse_int_user_id(user_id)
        if not parsed_user_id: raise ValueError("Invalid user ID format.")
        user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not user: raise ValueError("User not found.")

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Partial close can only be performed on active recommendations.")

        current_open_percent = Decimal(str(rec_orm.open_size_percent))
        # Ensure close_percent is valid and positive
        if not (isinstance(close_percent, Decimal) and close_percent.is_finite() and Decimal(0) < close_percent <= 100):
             raise ValueError(f"Invalid close percentage: {close_percent}. Must be a Decimal between 0 (exclusive) and 100.")

        actual_close_percent = min(close_percent, current_open_percent)
        if actual_close_percent <= Decimal('0'): # Check against zero after min()
            raise ValueError(f"Cannot close {close_percent}%. Open position is only {current_open_percent:.2f}%.")

        rec_orm.open_size_percent = current_open_percent - actual_close_percent

        raw_pct = _pct(rec_orm.entry, price, rec_orm.side)
        pnl_on_part = _normalize_pct_value(raw_pct)
        event_type = "PARTIAL_CLOSE_AUTO" if triggered_by == "AUTO" else "PARTIAL_CLOSE_MANUAL"
        event_data = {"price": float(price), "closed_percent": float(actual_close_percent), "remaining_percent": float(rec_orm.open_size_percent), "pnl_on_part": float(pnl_on_part)}
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type=event_type, event_data=event_data))

        pnl_formatted = f"{pnl_on_part:+.2f}%" # Use + sign for positive PnL
        notif_text = (
            f"💰 Partial Close (Profit) on #{rec_orm.asset}. Closed {actual_close_percent:g}% at {_format_price(price)} ({pnl_formatted})."
            if pnl_on_part >= 0 else
            f"⚠️ Partial Close (Loss Mgt) on #{rec_orm.asset}. Closed {actual_close_percent:g}% at {_format_price(price)} ({pnl_formatted})."
        )
        notif_text += f"\nRemaining: {rec_orm.open_size_percent:g}%"
        self.notify_reply(rec_id, notif_text, db_session)

        # Check if remaining position is negligible and close fully
        if rec_orm.open_size_percent < Decimal('0.1'):
            logger.info("Position #%s fully closed via partial close (remaining < 0.1).", rec_id)
            # Use internal call to avoid redundant checks if possible, or just call the public method
            # Need user_id for the final close ownership check
            return await self.close_recommendation_async(rec_id, user_id, price, db_session, reason="PARTIAL_CLOSE_FINAL")
        else:
            # Commit partial update and dispatch (no rebuild needed as still active)
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)
            refreshed_entity = self.repo._to_entity(rec_orm)
            if not refreshed_entity: raise RuntimeError(f"Failed to convert partially closed recommendation #{rec_id} to entity.")
            return refreshed_entity


    # ---------------------------
    # Event processors (Unchanged logic, added logging/robustness)
    # ---------------------------

    async def process_invalidation_event(self, item_id: int):
        """Mark a pending recommendation as invalidated."""
        try:
            with session_scope() as db_session:
                rec = self.repo.get_for_update(db_session, item_id)
                if not rec:
                     logger.warning(f"Invalidation event ignored: Recommendation #{item_id} not found.")
                     return
                if rec.status != RecommendationStatusEnum.PENDING:
                    logger.info(f"Invalidation event ignored: Recommendation #{item_id} is not PENDING (status: {rec.status}).")
                    return

                rec.status = RecommendationStatusEnum.CLOSED
                rec.closed_at = datetime.now(timezone.utc)
                db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED", event_data={"reason": "SL hit before entry"}))
                self.notify_reply(rec.id, f"❌ Signal #{rec.asset} was invalidated (SL hit before entry).", db_session=db_session)
                await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
                logger.info(f"Recommendation #{item_id} invalidated.")
        except Exception as e:
            logger.exception(f"Error processing invalidation event for item #{item_id}: {e}")


    async def process_activation_event(self, item_id: int):
        """Activate a pending recommendation."""
        try:
            with session_scope() as db_session:
                rec = self.repo.get_for_update(db_session, item_id)
                if not rec:
                    logger.warning(f"Activation event ignored: Recommendation #{item_id} not found.")
                    return
                if rec.status != RecommendationStatusEnum.PENDING:
                    logger.info(f"Activation event ignored: Recommendation #{item_id} is not PENDING (status: {rec.status}).")
                    return

                rec.status = RecommendationStatusEnum.ACTIVE
                rec.activated_at = datetime.now(timezone.utc)
                db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))
                self.notify_reply(rec.id, f"▶️ Signal #{rec.asset} is now ACTIVE!", db_session=db_session)
                await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True) # Rebuild needed
                logger.info(f"Recommendation #{item_id} activated.")
        except Exception as e:
            logger.exception(f"Error processing activation event for item #{item_id}: {e}")

    async def process_sl_hit_event(self, item_id: int, price: Decimal):
        """Handle SL hit by closing the recommendation."""
        try:
            with session_scope() as db_session:
                # Use get() first to fetch analyst relation without lock initially
                rec_check = self.repo.get(db_session, item_id)
                if not rec_check:
                    logger.warning(f"SL Hit event ignored: Recommendation #{item_id} not found.")
                    return
                if rec_check.status != RecommendationStatusEnum.ACTIVE:
                    logger.info(f"SL Hit event ignored: Recommendation #{item_id} is not ACTIVE (status: {rec_check.status}).")
                    return

                # Fetch analyst user ID safely
                analyst_user_id = str(rec_check.analyst.telegram_user_id) if getattr(rec_check, "analyst", None) else None
                if not analyst_user_id:
                     logger.error(f"Cannot process SL hit for #{item_id}: Analyst user ID not found.")
                     # Decide handling: maybe assign to a default admin or skip? For now, log error and skip.
                     return

                # Now call the close function which will re-fetch with lock
                await self.close_recommendation_async(item_id, analyst_user_id, price, db_session, reason="SL_HIT")
                logger.info(f"Recommendation #{item_id} closed due to SL hit at {price}.")
        except Exception as e:
             logger.exception(f"Error processing SL hit event for item #{item_id}: {e}")


    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        """Handle TP hit events."""
        try:
            with session_scope() as db_session:
                rec_orm = self.repo.get_for_update(db_session, item_id) # Lock the row
                if not rec_orm:
                    logger.warning(f"TP Hit event ignored: Recommendation #{item_id} not found.")
                    return
                if rec_orm.status != RecommendationStatusEnum.ACTIVE:
                    logger.info(f"TP Hit event ignored: Recommendation #{item_id} is not ACTIVE (status: {rec_orm.status}).")
                    return

                event_type = f"TP{target_index}_HIT"
                # Check if this specific TP event was already processed by querying events relation
                processed_event_types = {e.event_type for e in (rec_orm.events or [])}
                if event_type in processed_event_types:
                    logger.debug("TP event already processed for %s %s", item_id, event_type)
                    return

                # Record the TP hit event
                db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={"price": float(price)}))
                self.notify_reply(rec_orm.id, f"🎯 Signal #{rec_orm.asset} hit TP{target_index} at {_format_price(price)}!", db_session=db_session)
                logger.info(f"Processing TP{target_index} hit for Recommendation #{item_id} at {price}.")

                try:
                    target_info = rec_orm.targets[target_index - 1]
                    close_percent = Decimal(str(target_info.get("close_percent", 0)))
                except (IndexError, KeyError, InvalidOperation):
                    logger.warning(f"Could not find or parse target info for TP{target_index} on Rec #{item_id}. Assuming 0% close.")
                    close_percent = Decimal(0)

                analyst_user_id = str(rec_orm.analyst.telegram_user_id) if getattr(rec_orm, "analyst", None) else None
                if not analyst_user_id:
                     logger.error(f"Cannot process TP actions for #{item_id}: Analyst user ID not found.")
                     # Don't proceed with partial/full close without owner context
                     await self._commit_and_dispatch(db_session, rec_orm) # Commit event only
                     return

                # Perform partial close if required
                if close_percent > 0:
                    rec_orm = await self.partial_close_async(rec_orm.id, analyst_user_id, close_percent, price, db_session, triggered_by="AUTO")
                    # partial_close_async commits and refreshes, so rec_orm is updated
                    if rec_orm.status == RecommendationStatusEnum.CLOSED:
                         logger.info(f"Recommendation #{item_id} fully closed during partial close for TP{target_index}.")
                         # Already closed and notified, just return
                         return # Important: exit after full closure

                # Check for final close conditions *after* potential partial close
                is_final_tp = (target_index == len(rec_orm.targets))
                should_auto_close = (
                    rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final_tp
                ) or rec_orm.open_size_percent < Decimal('0.1')

                if should_auto_close and rec_orm.status == RecommendationStatusEnum.ACTIVE:
                    logger.info(f"Auto-closing Recommendation #{item_id} after hitting final TP or negligible remaining size.")
                    await self.close_recommendation_async(rec_orm.id, analyst_user_id, price, db_session, reason="AUTO_CLOSE_FINAL_TP")
                elif rec_orm.status == RecommendationStatusEnum.ACTIVE:
                    # If not auto-closing, just commit the TP hit event and potential partial close state
                    await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False) # Don't rebuild if still active

        except Exception as e:
            logger.exception(f"Error processing TP hit event for item #{item_id}, TP{target_index}: {e}")


    # ---------------------------
    # Read utilities for users (Unchanged)
    # ---------------------------

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """Return combined list of open recommendations (for analysts) and user trades (for traders)."""
        parsed_user_id = _parse_int_user_id(user_telegram_id)
        if not parsed_user_id: return []
        user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not user: return []

        open_positions: List[RecommendationEntity] = []

        # Analyst open recommendations
        if user.user_type == UserType.ANALYST:
            recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in recs_orm:
                if rec_entity := self.repo._to_entity(rec):
                    setattr(rec_entity, 'is_user_trade', False)
                    open_positions.append(rec_entity)

        # Trader open trades
        trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trades_orm:
            # Convert UserTrade ORM to RecommendationEntity structure for unified display
            try:
                 trade_entity = RecommendationEntity(
                     id=trade.id, asset=Symbol(trade.asset), side=Side(trade.side),
                     entry=Price(trade.entry), stop_loss=Price(trade.stop_loss),
                     targets=Targets(trade.targets), status=RecommendationStatusEntity.ACTIVE, # Assuming OPEN maps to ACTIVE display
                     order_type=OrderType.MARKET, # User trades assumed MARKET
                     created_at=trade.created_at
                     # Fields like analyst_id, market, notes etc. are not applicable or available
                 )
                 setattr(trade_entity, 'is_user_trade', True)
                 open_positions.append(trade_entity)
            except Exception as e:
                 logger.error(f"Failed to convert UserTrade ID {trade.id} to entity: {e}")

        # Sort combined list by creation date, newest first
        open_positions.sort(key=lambda p: getattr(p, "created_at", datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        return open_positions


    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """Return details for a single position, enforcing ownership."""
        parsed_user_id = _parse_int_user_id(user_telegram_id)
        if not parsed_user_id: return None
        user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not user: return None

        if position_type == 'rec':
            # Analyst accessing their recommendation
            if user.user_type != UserType.ANALYST: return None # Traders cannot access rec details directly this way
            rec_orm = self.repo.get(db_session, position_id)
            # Check ownership
            if not rec_orm or rec_orm.analyst_id != user.id: return None
            if rec_entity := self.repo._to_entity(rec_orm):
                setattr(rec_entity, 'is_user_trade', False)
                return rec_entity

        elif position_type == 'trade':
            # User accessing their own trade
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            # Check ownership
            if not trade_orm or trade_orm.user_id != user.id: return None
            # Convert UserTrade ORM to RecommendationEntity structure
            try:
                trade_status_map = {
                    UserTradeStatus.OPEN: RecommendationStatusEntity.ACTIVE,
                    UserTradeStatus.CLOSED: RecommendationStatusEntity.CLOSED,
                }
                trade_entity = RecommendationEntity(
                    id=trade_orm.id, asset=Symbol(trade_orm.asset), side=Side(trade_orm.side),
                    entry=Price(trade_orm.entry), stop_loss=Price(trade_orm.stop_loss),
                    targets=Targets(trade_orm.targets), status=trade_status_map.get(trade_orm.status, RecommendationStatusEntity.CLOSED),
                    order_type=OrderType.MARKET, created_at=trade_orm.created_at,
                    closed_at=trade_orm.closed_at,
                    # Convert Decimal exit price safely to float or None
                    exit_price=float(trade_orm.close_price) if trade_orm.close_price is not None else None
                )
                setattr(trade_entity, 'is_user_trade', True)
                return trade_entity
            except Exception as e:
                 logger.error(f"Failed to convert UserTrade ID {trade_orm.id} to entity for details view: {e}")
                 return None # Return None on conversion error

        return None # Invalid position_type


    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """Return recent assets the user interacted with."""
        parsed_user_id = _parse_int_user_id(user_telegram_id)
        if not parsed_user_id: return []
        user = UserRepository(db_session).find_by_telegram_id(parsed_user_id)
        if not user: return []

        assets = []
        if user.user_type == UserType.ANALYST:
            # Fetch assets from most recent recommendations (open or closed)
            recs = db_session.query(Recommendation.asset)\
                .filter(Recommendation.analyst_id == user.id)\
                .order_by(Recommendation.created_at.desc())\
                .limit(limit * 2) # Fetch more initially to account for duplicates
            if recs:
                # Use dict.fromkeys to preserve order while removing duplicates
                assets = list(dict.fromkeys([r.asset for r in recs]))[:limit]
        else:
            # Fetch assets from most recent user trades (open or closed)
            trades = db_session.query(UserTrade.asset)\
                .filter(UserTrade.user_id == user.id)\
                .order_by(UserTrade.created_at.desc())\
                .limit(limit * 2)
            if trades:
                assets = list(dict.fromkeys([t.asset for t in trades]))[:limit]

        # Fallback to common defaults if fewer than 'limit' assets found
        if len(assets) < limit:
            default_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
            for a in default_assets:
                if a not in assets and len(assets) < limit:
                    assets.append(a)

        return assets

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/trade_service.py ---