# --- src/capitalguard/application/services/trade_service.py ---
# src/capitalguard/application/services/trade_service.py v 30.9 - FINAL
"""
TradeService v30.9 - Final, complete, and production-ready version.
✅ Includes `create_trade_from_forwarding_async` and `close_user_trade_async`.
✅ Contains fixes for Decimal handling, deep-linking, validation, and helpers.
"""

from __future__ import annotations
import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    PublishedMessage, Recommendation, RecommendationEvent, User,
    RecommendationStatusEnum, UserTrade, UserTradeStatus, OrderTypeEnum, ExitStrategyEnum
) #
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, ChannelRepository, UserRepository
) #
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType, ExitStrategy, UserType
) #
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets #

# Type-only imports for services injected at runtime
if False:
    from .alert_service import AlertService #
    from .price_service import PriceService #
    from .market_data_service import MarketDataService #

logger = logging.getLogger(__name__)

# ---------------------------
# Internal Helper Functions
# ---------------------------

def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """Safely convert any value to a Decimal, returning default on failure."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    if value is None:
        return default
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError):
        logger.debug(f"Could not convert '{value}' to Decimal, using default '{default}'.")
        return default

def _format_price(price: Any) -> str:
    """Formats a Decimal or number into a clean string (e.g., no trailing zeros)."""
    price_dec = _to_decimal(price)
    if not price_dec.is_finite():
        return "N/A"
    # Use 'g' for general format, removes trailing zeros automatically
    return f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    """Computes percentage PnL from entry to target_price."""
    try:
        entry_dec = _to_decimal(entry)
        target_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite():
            return 0.0
        side_upper = (str(side) or "").upper() # Ensure side is string
        if side_upper == "LONG":
            pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT":
            pnl = ((entry_dec / target_dec) - 1) * 100
        else:
            return 0.0
        return float(pnl) # Return as float
    except (InvalidOperation, TypeError, ZeroDivisionError) as exc:
        logger.debug(f"pct calc error: entry={entry}, target={target_price}, side={side}, error={exc}")
        return 0.0

def _normalize_pct_value(pct_raw: Any) -> Decimal:
    """Normalize a raw percentage value (number, string with %) into Decimal."""
    try:
        if isinstance(pct_raw, Decimal): return pct_raw
        if isinstance(pct_raw, (int, float)): return Decimal(str(pct_raw))
        if isinstance(pct_raw, str):
            s = pct_raw.strip().replace('%', '').replace('+', '').replace(',', '')
            return Decimal(s)
        return Decimal(str(pct_raw))
    except (InvalidOperation, Exception) as exc:
        logger.warning(f"Unable to normalize pct value '{pct_raw}' ({exc}); defaulting to 0")
        return Decimal(0)

def _parse_int_user_id(user_id: Any) -> Optional[int]:
    """Safely parse numeric telegram user id from various inputs."""
    try:
        if user_id is None: return None
        user_str = str(user_id).strip()
        # Check if it's a valid integer, potentially negative (like channel IDs)
        return int(user_str) if user_str.lstrip('-').isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None

# ---------------------------
# TradeService Class
# ---------------------------

class TradeService:
    """Manages the lifecycle of Recommendations and UserTrades."""
    def __init__(
        self,
        # Pass Repository *instances* - assuming UOW manages sessions externally
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: "MarketDataService",
        price_service: "PriceService",
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        # Injected after initialization by build_services
        self.alert_service: Optional["AlertService"] = None

    # --- Internal DB / Notifier Helpers ---
    async def _commit_and_dispatch(self, db_session: Session, rec_orm: Recommendation, rebuild_alerts: bool = True):
        """Commits changes, refreshes ORM, updates alerts, notifies UI."""
        try:
            db_session.commit()
            db_session.refresh(rec_orm) # Refresh to get latest state after commit
            logger.debug(f"Committed changes for Recommendation ID {rec_orm.id}")
        except Exception as commit_err:
            logger.error(f"Commit failed for Recommendation ID {rec_orm.id}: {commit_err}", exc_info=True)
            db_session.rollback() # Rollback on commit error
            raise # Re-raise after rollback

        if rebuild_alerts and self.alert_service:
            try:
                await self.alert_service.build_triggers_index()
            except Exception as alert_err:
                logger.exception(f"Failed to rebuild alerts index after commit for Rec ID {rec_orm.id}: {alert_err}")

        # Convert to entity only after successful commit and refresh
        updated_entity = self.repo._to_entity(rec_orm)
        if updated_entity:
            try:
                await self.notify_card_update(updated_entity, db_session)
            except Exception as notify_err:
                logger.exception(f"Failed to notify card update for Rec ID {rec_orm.id}: {notify_err}")
        else:
             logger.error(f"Failed to convert ORM Rec ID {rec_orm.id} to entity after commit.")


    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """Calls notifier function (sync or async)."""
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        else:
            # Run synchronous notifier functions in a thread pool executor
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        """Updates all published Telegram messages for a recommendation."""
        if getattr(rec_entity, "is_shadow", False): return

        try:
            # Fetch messages within the same session if possible, or new scope if needed
            published_messages = self.repo.get_published_messages(db_session, rec_entity.id)
            if not published_messages: return

            tasks = [
                self._call_notifier_maybe_async(
                    self.notifier.edit_recommendation_card_by_ids,
                    channel_id=msg.telegram_channel_id,
                    message_id=msg.telegram_message_id,
                    rec=rec_entity
                ) for msg in published_messages
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    # Log individual notification failures
                    logger.error(f"notify_card_update task failed for Rec ID {rec_entity.id}: {res}", exc_info=False)
        except Exception as e:
             logger.error(f"Error fetching published messages for Rec ID {rec_entity.id}: {e}", exc_info=True)


    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        """Posts a reply to all published messages (fire-and-forget)."""
        # Fetch ORM within the provided session to check shadow status
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or getattr(rec_orm, "is_shadow", False):
            return

        published_messages = self.repo.get_published_messages(db_session, rec_id)
        for msg in published_messages:
            # Create task without awaiting
            asyncio.create_task(self._call_notifier_maybe_async(
                self.notifier.post_notification_reply,
                chat_id=msg.telegram_channel_id,
                message_id=msg.telegram_message_id,
                text=text
            ))

    # --- Validation ---
    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        """Strict validation for recommendation numerical integrity. Raises ValueError."""
        side_upper = (str(side) or "").upper()
        if not all(v is not None and isinstance(v, Decimal) and v.is_finite() and v > 0 for v in [entry, stop_loss]):
            raise ValueError("Entry and Stop Loss must be positive finite Decimal values.")

        if not targets or not isinstance(targets, list):
            raise ValueError("Targets must be a non-empty list of dictionaries.")

        target_prices: List[Decimal] = []
        total_close_pct = 0.0
        for i, t in enumerate(targets):
            if not isinstance(t, dict) or 'price' not in t:
                raise ValueError(f"Target {i+1} has invalid format. Expected {{'price': Decimal, ...}}.")
            price = _to_decimal(t.get('price'))
            if not price.is_finite() or price <= 0:
                raise ValueError(f"Target {i+1} price must be a positive finite Decimal.")
            target_prices.append(price)
            # Validate close percent format/range
            close_pct = t.get('close_percent', 0.0)
            try:
                 close_pct_float = float(close_pct)
                 if not (0.0 <= close_pct_float <= 100.0):
                      raise ValueError(f"Target {i+1} close_percent ({close_pct_float}%) must be between 0 and 100.")
                 total_close_pct += close_pct_float
            except (ValueError, TypeError):
                 raise ValueError(f"Target {i+1} close_percent ('{close_pct}') must be a valid number.")

        # Optionally, enforce that total close percent sums to 100 for non-manual strategies?
        # For now, we only validate individual targets.

        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("For LONG, Stop Loss must be less than Entry.")
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("For SHORT, Stop Loss must be greater than Entry.")

        if side_upper == "LONG" and any(p <= entry for p in target_prices):
            raise ValueError("All LONG targets must be greater than the entry price.")
        if side_upper == "SHORT" and any(p >= entry for p in target_prices):
            raise ValueError("All SHORT targets must be less than the entry price.")

        risk = abs(entry - stop_loss)
        if risk.is_zero():
            raise ValueError("Entry and Stop Loss cannot be the same price.")

        first_target_price = min(target_prices) if side_upper == "LONG" else max(target_prices)
        reward = abs(first_target_price - entry)
        if reward.is_zero() or (reward / risk) < Decimal('0.1'): # Avoid division by zero, enforce min R:R
            raise ValueError("Risk/Reward ratio to first target is too low (minimum 0.1).")

        if len(target_prices) != len(set(target_prices)):
            raise ValueError("Target prices must be unique.")

        sorted_prices = sorted(target_prices, reverse=(side_upper == 'SHORT'))
        if target_prices != sorted_prices:
            raise ValueError("Targets must be sorted (ascending for LONG, descending for SHORT).")


    # --- Publishing ---
    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_db_id: int, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """Publishes a recommendation entity to specified or all active analyst channels."""
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        # Assuming user_db_id is the internal DB ID
        channels_to_publish = ChannelRepository(session).list_by_analyst(user_db_id, only_active=True)

        if target_channel_ids is not None: # Filter if specific channels requested
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]

        if not channels_to_publish:
            report["failed"].append({"reason": "No active channels linked or selected."})
            return rec_entity, report

        try:
            from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
            keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        except ImportError:
            keyboard = None
            logger.warning("Could not import public_channel_keyboard, proceeding without keyboard.")

        publish_tasks = []
        channel_map = {ch.telegram_channel_id: ch for ch in channels_to_publish}

        for channel_id in channel_map.keys():
            publish_tasks.append(self._call_notifier_maybe_async(
                self.notifier.post_to_channel, channel_id, rec_entity, keyboard
            ))

        results = await asyncio.gather(*publish_tasks, return_exceptions=True)

        for i, channel_id in enumerate(channel_map.keys()):
            result = results[i]
            if isinstance(result, Exception):
                logger.exception(f"Failed to publish Rec {rec_entity.id} to channel {channel_id}: {result}")
                report["failed"].append({"channel_id": channel_id, "reason": str(result)})
            elif isinstance(result, tuple) and len(result) == 2:
                # result is (chat_id, message_id)
                session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=result[0], telegram_message_id=result[1]))
                report["success"].append({"channel_id": channel_id, "message_id": result[1]})
            else:
                reason = f"Notifier returned unexpected result: {type(result)}"
                logger.error(f"Failed to publish Rec {rec_entity.id} to channel {channel_id}: {reason}")
                report["failed"].append({"channel_id": channel_id, "reason": reason})

        session.flush() # Ensure PublishedMessage records are flushed before returning
        return rec_entity, report

    # --- Public API - Create/Publish ---
    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """Creates and publishes a new recommendation."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user or user.user_type != UserTypeEntity.ANALYST:
            raise ValueError("Only analysts can create recommendations.")

        # Extract and validate data using _to_decimal
        entry_price = _to_decimal(kwargs['entry'])
        sl_price = _to_decimal(kwargs['stop_loss'])
        # Ensure targets passed validation and convert prices to Decimal
        targets_list_in = kwargs['targets']
        targets_list_validated = [{'price': _to_decimal(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_in]

        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        order_type_enum = OrderTypeEnum[kwargs['order_type'].upper()]

        # Normalize exit strategy
        exit_strategy_val = kwargs.get('exit_strategy')
        if exit_strategy_val is None: exit_strategy_enum = ExitStrategyEnum.CLOSE_AT_FINAL_TP
        elif isinstance(exit_strategy_val, ExitStrategyEnum): exit_strategy_enum = exit_strategy_val
        elif isinstance(exit_strategy_val, ExitStrategy): exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.name]
        elif isinstance(exit_strategy_val, str):
            try: exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.upper()]
            except KeyError: raise ValueError(f"Unsupported exit_strategy string: {exit_strategy_val}")
        else: raise ValueError(f"Unsupported exit_strategy format: {type(exit_strategy_val)}")

        # Handle MARKET order entry price
        if order_type_enum == OrderTypeEnum.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            status, final_entry = RecommendationStatusEnum.ACTIVE, _to_decimal(live_price) if live_price is not None else None
            if final_entry is None or not final_entry.is_finite() or final_entry <= 0:
                raise RuntimeError(f"Could not fetch valid live market price for {asset}.")
        else:
            status, final_entry = RecommendationStatusEnum.PENDING, entry_price

        # Perform final validation with potentially updated entry price
        self._validate_recommendation_data(side, final_entry, sl_price, targets_list_validated)

        # Convert targets back to DB format (strings/floats)
        targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_validated]

        rec_orm = Recommendation(
            analyst_id=user.id, asset=asset, side=side, entry=final_entry, stop_loss=sl_price,
            targets=targets_for_db, order_type=order_type_enum, status=status, market=market,
            notes=kwargs.get('notes'), exit_strategy=exit_strategy_enum,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )
        db_session.add(rec_orm)
        db_session.flush() # Get rec_orm.id
        db_session.add(RecommendationEvent(
            recommendation_id=rec_orm.id,
            event_type="CREATED_ACTIVE" if status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING",
            event_data={'entry': str(final_entry)} # Log the actual entry price
        ))
        db_session.flush()
        db_session.refresh(rec_orm) # Refresh to load default values like created_at

        created_rec_entity = self.repo._to_entity(rec_orm)
        if not created_rec_entity:
             # This should ideally not happen if ORM creation succeeded
             raise RuntimeError(f"Failed to convert newly created ORM Rec ID {rec_orm.id} to entity.")

        final_rec, report = await self._publish_recommendation(
             db_session, created_rec_entity, user.id, kwargs.get('target_channel_ids')
        )

        if self.alert_service:
            try: await self.alert_service.build_triggers_index()
            except Exception: logger.exception("alert_service.build_triggers_index failed after create")

        return final_rec, report


    # --- User Trade Functions ( ✅ NEW & Updated ) ---
    async def create_trade_from_forwarding_async(
        self,
        user_id: str, # Telegram User ID as string
        trade_data: Dict[str, Any], # Data from ParsingResult.data (with Decimals)
        original_text: Optional[str],
        db_session: Session
    ) -> Dict[str, Any]:
        """Creates a UserTrade from parsed forwarded data."""
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}

        try:
            # Data from ParsingResult.data contains Decimals
            entry_dec = trade_data['entry']
            sl_dec = trade_data['stop_loss']
            targets_list_validated = trade_data['targets'] # Already list of dicts {'price': Decimal, '%': float}

            # Validate the structure and logic
            self._validate_recommendation_data(trade_data['side'], entry_dec, sl_dec, targets_list_validated)

            # Convert targets back to DB format (strings/floats)
            targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_validated]

            new_trade = UserTrade(
                user_id=trader_user.id,
                asset=trade_data['asset'],
                side=trade_data['side'],
                entry=entry_dec,
                stop_loss=sl_dec,
                targets=targets_for_db,
                status=UserTradeStatus.OPEN,
                source_forwarded_text=original_text
            )
            db_session.add(new_trade)
            db_session.flush() # Get the new ID
            log.info(f"UserTrade {new_trade.id} created for user {user_id} from forwarded message.")
            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        except ValueError as e:
            logger.warning(f"Validation failed for forwarded trade data for user {user_id}: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"Error creating trade from forwarding for user {user_id}: {e}", exc_info=True)
            return {'success': False, 'error': 'Internal error saving trade.'}

    async def create_trade_from_recommendation(self, user_id: str, rec_id: int, db_session: Session) -> Dict[str, Any]:
        """Creates a UserTrade by tracking an existing Recommendation."""
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user: return {'success': False, 'error': 'User not found'}

        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm: return {'success': False, 'error': 'Signal not found'}

        # Prevent duplicate tracking
        existing_trade = self.repo.find_user_trade_by_source_id(db_session, trader_user.id, rec_id)
        if existing_trade: return {'success': False, 'error': 'You are already tracking this signal.'}

        try:
            new_trade = UserTrade(
                user_id=trader_user.id,
                asset=rec_orm.asset, side=rec_orm.side, entry=rec_orm.entry, stop_loss=rec_orm.stop_loss,
                targets=rec_orm.targets, # Assumes targets JSON is compatible
                status=UserTradeStatus.OPEN,
                source_recommendation_id=rec_orm.id
            )
            db_session.add(new_trade)
            db_session.flush()
            log.info(f"UserTrade {new_trade.id} created for user {user_id} tracking Rec {rec_id}.")
            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        except Exception as e:
            logger.error(f"Error creating trade from recommendation for user {user_id}, rec {rec_id}: {e}", exc_info=True)
            return {'success': False, 'error': 'Internal error tracking signal.'}

    async def close_user_trade_async(
        self,
        user_id: str, # Telegram User ID
        trade_id: int,
        exit_price: Decimal,
        db_session: Session
    ) -> Optional[UserTrade]:
        """Closes a UserTrade owned by the user. Returns updated ORM object or None."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")

        # Query and lock the specific trade row
        trade = db_session.query(UserTrade).filter(
            UserTrade.id == trade_id,
            UserTrade.user_id == user.id # Ensure ownership
        ).with_for_update().first()

        if not trade: raise ValueError(f"Trade #{trade_id} not found or access denied.")
        if trade.status == UserTradeStatus.CLOSED:
            logger.warning(f"Attempted to close already closed UserTrade #{trade_id}")
            return trade # Idempotent: return existing closed trade

        # Validate exit price
        if not exit_price.is_finite() or exit_price <= 0:
            raise ValueError("Exit price must be a positive finite number.")

        trade.status = UserTradeStatus.CLOSED
        trade.close_price = exit_price
        trade.closed_at = datetime.now(timezone.utc)

        # Calculate and store final PnL
        try:
            # Use Decimal for calculation if entry is Decimal
            entry_for_calc = trade.entry if isinstance(trade.entry, Decimal) else _to_decimal(trade.entry)
            pnl = _pct(entry_for_calc, exit_price, trade.side)
            # Store PnL with appropriate precision
            trade.pnl_percentage = Decimal(f"{pnl:.4f}")
        except Exception as calc_err:
            logger.error(f"Failed to calculate PnL for UserTrade {trade_id}: {calc_err}")
            trade.pnl_percentage = None # Store None if calculation fails

        logger.info(f"UserTrade {trade_id} closed for user {user_id} at price {exit_price}")
        db_session.flush() # Ensure changes are flushed before returning
        return trade

    # --- Update Operations (Analyst) ---
    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Optional[Session] = None) -> RecommendationEntity:
        """Analyst updates Stop Loss."""
        # Use session_scope if no session provided
        if db_session is None:
            with session_scope() as s: return await self.update_sl_for_user_async(rec_id, user_id, new_sl, s)

        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id) # Lock row
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied: Not owner.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")

        old_sl = rec_orm.stop_loss # Keep original (likely Decimal)
        try:
            # Validate new SL against current state
            targets_list = [{'price': _to_decimal(t.get('price')), 'close_percent': t.get('close_percent', 0.0)} for t in (rec_orm.targets or [])]
            self._validate_recommendation_data(rec_orm.side, rec_orm.entry, new_sl, targets_list)
        except ValueError as e:
            logger.warning(f"Invalid SL update for rec #{rec_id} by user {user_id}: {e}")
            raise ValueError(f"Invalid new Stop Loss: {e}")

        rec_orm.stop_loss = new_sl # Assign new Decimal value
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="SL_UPDATED", event_data={"old": str(old_sl), "new": str(new_sl)}))
        self.notify_reply(rec_id, f"⚠️ Stop Loss for #{rec_orm.asset} updated to {_format_price(new_sl)}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm) # Return updated entity

    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, Any]], db_session: Session) -> RecommendationEntity:
        """Analyst updates Targets."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Can only modify ACTIVE recommendations.")

        try:
            # Validate new targets structure and values
            targets_validated = [{'price': _to_decimal(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in new_targets]
            self._validate_recommendation_data(rec_orm.side, rec_orm.entry, rec_orm.stop_loss, targets_validated)
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Invalid TP update for rec #{rec_id} by user {user_id}: {e}")
            raise ValueError(f"Invalid new Targets format or values: {e}")

        old_targets_json = rec_orm.targets # Keep original JSON for event log
        # Convert validated targets back to strings for DB storage
        rec_orm.targets = [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in targets_validated]
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="TP_UPDATED", event_data={"old": old_targets_json, "new": rec_orm.targets}))
        self.notify_reply(rec_id, f"🎯 Targets for #{rec_orm.asset} have been updated.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_entry_and_notes_async(self, rec_id: int, user_id: str, new_entry: Optional[Decimal], new_notes: Optional[str], db_session: Session) -> RecommendationEntity:
        """Update entry (PENDING only) and notes."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED: raise ValueError("Cannot edit closed recommendation.")

        event_data = {}
        updated = False

        if new_entry is not None:
            if rec_orm.status != RecommendationStatusEnum.PENDING:
                raise ValueError("Entry price can only be modified for PENDING recommendations.")
            try:
                targets_list = [{'price': _to_decimal(t.get('price')), 'close_percent': t.get('close_percent', 0.0)} for t in (rec_orm.targets or [])]
                self._validate_recommendation_data(rec_orm.side, new_entry, rec_orm.stop_loss, targets_list)
            except ValueError as e:
                raise ValueError(f"Invalid new Entry Price: {e}")
            if rec_orm.entry != new_entry:
                event_data.update({"old_entry": str(rec_orm.entry), "new_entry": str(new_entry)})
                rec_orm.entry = new_entry
                updated = True

        # Check if notes changed or are explicitly cleared
        if new_notes is not None or (new_notes is None and rec_orm.notes is not None):
            if rec_orm.notes != new_notes:
                event_data.update({"old_notes": rec_orm.notes, "new_notes": new_notes})
                rec_orm.notes = new_notes
                updated = True

        if updated:
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="DATA_UPDATED", event_data=event_data))
            self.notify_reply(rec_id, f"✏️ Data for #{rec_orm.asset} updated.", db_session)
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=(new_entry is not None)) # Rebuild only if entry changed
        else:
            logger.debug(f"No changes detected for update_entry_and_notes on Rec ID {rec_id}.")


        return self.repo._to_entity(rec_orm) # Return entity even if no changes

    async def set_exit_strategy_async(self, rec_id: int, user_id: str, mode: str, price: Optional[Decimal] = None, trailing_value: Optional[Decimal] = None, active: bool = True, session: Optional[Session] = None) -> RecommendationEntity:
        """Set or cancel an exit strategy (e.g., profit stop)."""
        if session is None:
            with session_scope() as s: return await self.set_exit_strategy_async(rec_id, user_id, mode, price, trailing_value, active, s)

        user = UserRepository(session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec = self.repo.get_for_update(session, rec_id)
        if not rec: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec.analyst_id != user.id: raise ValueError("Access denied.")
        if rec.status != RecommendationStatusEnum.ACTIVE and active: # Allow cancelling even if PENDING? For now, only ACTIVE.
            raise ValueError("Exit strategies require ACTIVE recommendations.")

        mode_upper = mode.upper()
        # Basic validation for strategy parameters
        if mode_upper == "FIXED" and (price is None or not price.is_finite() or price <= 0):
             raise ValueError("Fixed profit stop requires a valid positive price.")
        if mode_upper == "TRAILING" and (trailing_value is None or not trailing_value.is_finite() or trailing_value <= 0):
             raise ValueError("Trailing stop requires a valid positive distance/percentage.")

        rec.profit_stop_mode = mode_upper if active else "NONE" # Set to NONE if cancelling
        rec.profit_stop_price = price if active and mode_upper == "FIXED" else None
        rec.profit_stop_trailing_value = trailing_value if active and mode_upper == "TRAILING" else None
        rec.profit_stop_active = active

        event_data = {"mode": rec.profit_stop_mode, "active": active}
        if rec.profit_stop_price: event_data["price"] = str(rec.profit_stop_price)
        if rec.profit_stop_trailing_value: event_data["trailing_value"] = str(rec.profit_stop_trailing_value)
        session.add(RecommendationEvent(recommendation_id=rec_id, event_type="EXIT_STRATEGY_UPDATED", event_data=event_data))

        if active:
            msg = f"📈 Exit strategy for #{rec.asset} set to: {mode_upper}"
            if mode_upper == "FIXED": msg += f" at {_format_price(price)}"
            elif mode_upper == "TRAILING": msg += f" with value {_format_price(trailing_value)}"
            self.notify_reply(rec_id, msg, session)
        else:
            self.notify_reply(rec_id, f"❌ Exit strategy for #{rec.asset} cancelled.", session)

        await self._commit_and_dispatch(session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)

    # --- Automation Helpers ---
    async def move_sl_to_breakeven_async(self, rec_id: int, db_session: Optional[Session] = None) -> RecommendationEntity:
        """Moves SL to entry +/- buffer if conditions met."""
        if db_session is None:
            with session_scope() as s: return await self.move_sl_to_breakeven_async(rec_id, s)

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Can only move SL to BE for ACTIVE recommendations.")

        entry_dec = _to_decimal(rec_orm.entry)
        current_sl_dec = _to_decimal(rec_orm.stop_loss)
        if not entry_dec.is_finite() or entry_dec <= 0 or not current_sl_dec.is_finite():
             raise ValueError("Invalid entry or stop loss price for breakeven calculation.")

        # Calculate buffer (e.g., 0.01% of entry price, minimum 1 tick?) - Needs refinement based on asset precision
        buffer = entry_dec * Decimal('0.0001') # Example: 0.01% buffer
        # Ensure buffer respects tick size if available (requires exchange info)

        new_sl_target = entry_dec
        if rec_orm.side == 'LONG':
            new_sl_target = entry_dec + buffer # Move slightly above entry for LONG BE
        elif rec_orm.side == 'SHORT':
            new_sl_target = entry_dec - buffer # Move slightly below entry for SHORT BE

        # Check if new SL is actually an improvement (further from current price in safe direction)
        is_improvement = False
        if rec_orm.side == 'LONG' and new_sl_target > current_sl_dec:
            is_improvement = True
        elif rec_orm.side == 'SHORT' and new_sl_target < current_sl_dec:
            is_improvement = True

        if is_improvement:
            analyst_uid = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
            if not analyst_uid:
                raise RuntimeError(f"Cannot perform BE for Rec ID {rec_id}: Analyst info missing.")
            logger.info(f"Moving SL to BE for Rec #{rec_id} from {current_sl_dec:g} to {new_sl_target:g}")
            # Use update_sl_for_user_async which includes validation
            return await self.update_sl_for_user_async(rec_id, analyst_uid, new_sl_target, db_session)
        else:
            logger.info(f"SL for Rec #{rec_id} is already at or better than breakeven target {new_sl_target:g}. No action.")
            return self.repo._to_entity(rec_orm) # Return current state


    # --- Closing Operations ---
    async def close_recommendation_async(self, rec_id: int, user_id: Optional[str], exit_price: Decimal, db_session: Optional[Session] = None, reason: str = "MANUAL_CLOSE") -> RecommendationEntity:
        """Closes a recommendation fully."""
        if db_session is None:
            with session_scope() as s: return await self.close_recommendation_async(rec_id, user_id, exit_price, s, reason)

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            logger.warning(f"Attempted to close already closed recommendation #{rec_id}")
            return self.repo._to_entity(rec_orm)

        # Ownership check if user_id is provided
        if user_id is not None:
            user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
            if not user or rec_orm.analyst_id != user.id: raise ValueError("Access denied.")

        # Ensure exit price is valid
        if not exit_price.is_finite() or exit_price <= 0:
             raise ValueError("Exit price must be a positive finite number.")

        remaining_percent = _to_decimal(rec_orm.open_size_percent)
        if remaining_percent > 0:
            pnl_on_part = _pct(rec_orm.entry, exit_price, rec_orm.side)
            event_data = {
                 "price": float(exit_price), # Store as float in JSON
                 "closed_percent": float(remaining_percent),
                 "pnl_on_part": pnl_on_part,
                 "triggered_by": reason
            }
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="FINAL_CLOSE", event_data=event_data))

        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.exit_price = exit_price
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.open_size_percent = Decimal(0)
        rec_orm.profit_stop_active = False # Deactivate any profit stop

        self.notify_reply(rec_id, f"✅ Signal #{rec_orm.asset} closed at {_format_price(exit_price)}. Reason: {reason}", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)


    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, price: Decimal, db_session: Session, triggered_by: str = "MANUAL") -> RecommendationEntity:
        """Partially closes a recommendation."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Partial close requires ACTIVE recommendations.")

        current_open_percent = _to_decimal(rec_orm.open_size_percent)
        # Ensure close_percent and price are valid Decimals
        close_percent_dec = _to_decimal(close_percent)
        price_dec = _to_decimal(price)
        if not (close_percent_dec.is_finite() and 0 < close_percent_dec <= 100):
             raise ValueError("Close percentage must be between 0 and 100.")
        if not (price_dec.is_finite() and price_dec > 0):
             raise ValueError("Close price must be a positive number.")

        actual_close_percent = min(close_percent_dec, current_open_percent)
        if actual_close_percent <= 0:
             raise ValueError(f"Invalid percentage. Open position is {current_open_percent:g}%. Cannot close {close_percent_dec:g}%.")

        rec_orm.open_size_percent = current_open_percent - actual_close_percent
        pnl_on_part = _pct(rec_orm.entry, price_dec, rec_orm.side)
        pnl_formatted = f"{pnl_on_part:+.2f}%"

        event_type = "PARTIAL_CLOSE_AUTO" if triggered_by.upper() == "AUTO" else "PARTIAL_CLOSE_MANUAL"
        event_data = {
            "price": float(price_dec), "closed_percent": float(actual_close_percent),
            "remaining_percent": float(rec_orm.open_size_percent), "pnl_on_part": pnl_on_part
        }
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type=event_type, event_data=event_data))

        notif_icon = "💰 Profit" if pnl_on_part >= 0 else "⚠️ Loss Mgt"
        notif_text = (
            f"{notif_icon} Partial Close on #{rec_orm.asset}. "
            f"Closed {actual_close_percent:g}% at {_format_price(price_dec)} ({pnl_formatted}).\n"
            f"Remaining: {rec_orm.open_size_percent:g}%"
        )
        self.notify_reply(rec_id, notif_text, db_session)

        # Check if remaining position is negligible, then fully close
        if rec_orm.open_size_percent < Decimal('0.1'):
            logger.info(f"Position #{rec_id} fully closed via partial close (remaining < 0.1%).")
            # Use the same price from this partial close for the final close
            return await self.close_recommendation_async(rec_id, user_id, price_dec, db_session, reason="PARTIAL_CLOSE_FINAL")
        else:
            # Commit partial close without rebuilding alerts index (already covered by potential final close)
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)
            return self.repo._to_entity(rec_orm)


    # --- Event Processors (Called by AlertService/StrategyEngine) ---
    async def process_invalidation_event(self, item_id: int):
        """Marks a PENDING recommendation as CLOSED (invalidated)."""
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING: return
            rec.status = RecommendationStatusEnum.CLOSED
            rec.closed_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED", event_data={"reason": "SL hit before entry"}))
            self.notify_reply(rec.id, f"❌ Signal #{rec.asset} invalidated (SL hit before entry).", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec) # Rebuild alerts needed


    async def process_activation_event(self, item_id: int):
        """Activates a PENDING recommendation."""
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING: return
            rec.status = RecommendationStatusEnum.ACTIVE
            rec.activated_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))
            self.notify_reply(rec.id, f"▶️ Signal #{rec.asset} is now ACTIVE!", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec) # Rebuild alerts needed


    async def process_sl_hit_event(self, item_id: int, price: Decimal):
        """Handles SL hit event by closing the recommendation."""
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.ACTIVE: return
            # Close internally (no user_id needed for system events)
            await self.close_recommendation_async(rec.id, None, price, db_session, reason="SL_HIT")


    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        """Handles TP hit: logs event, triggers partial/full close based on strategy."""
        with session_scope() as db_session:
            rec_orm = self.repo.get_for_update(db_session, item_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE: return

            event_type = f"TP{target_index}_HIT"
            # Idempotency check: Don't process same TP hit twice
            if any(e.event_type == event_type for e in (rec_orm.events or [])):
                logger.debug(f"TP event already processed for Rec ID {item_id} - {event_type}")
                return

            db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={"price": float(price)}))
            self.notify_reply(rec_orm.id, f"🎯 Signal #{rec_orm.asset} hit TP{target_index} at {_format_price(price)}!", db_session=db_session)

            try:
                target_info = rec_orm.targets[target_index - 1]
            except (IndexError, TypeError, KeyError):
                target_info = {}

            close_percent = _to_decimal(target_info.get("close_percent", 0))

            # Trigger partial close if configured
            analyst_uid_str = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
            if not analyst_uid_str:
                 logger.error(f"Cannot perform auto partial/full close for TP hit on Rec ID {rec_orm.id}: Analyst info missing.")
                 # Commit event and notification only
                 await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)
                 return

            if close_percent > 0:
                await self.partial_close_async(rec_orm.id, analyst_uid_str, close_percent, price, db_session, triggered_by="AUTO")
                # Refresh ORM state after potential partial close
                db_session.refresh(rec_orm)

            # Check for final close conditions
            is_final_tp = (target_index == len(rec_orm.targets or []))
            should_auto_close = (rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final_tp)
            is_effectively_closed = (rec_orm.open_size_percent is not None and rec_orm.open_size_percent < Decimal('0.1'))

            if should_auto_close or is_effectively_closed:
                # Ensure it's not already closed by the partial_close call
                if rec_orm.status == RecommendationStatusEnum.ACTIVE:
                    reason = "AUTO_CLOSE_FINAL_TP" if should_auto_close else "CLOSED_VIA_PARTIAL"
                    await self.close_recommendation_async(rec_orm.id, analyst_uid_str, price, db_session, reason=reason)
            elif close_percent <= 0: # If TP hit didn't trigger partial close, still commit event
                 await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)


    # --- Read Utilities ---
    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """Returns combined list of open Recommendations and UserTrades for a user."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: return []

        open_positions: List[RecommendationEntity] = []

        # Analyst's Recommendations
        if user.user_type == UserTypeEntity.ANALYST:
            recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in recs_orm:
                if rec_entity := self.repo._to_entity(rec):
                    setattr(rec_entity, 'is_user_trade', False)
                    open_positions.append(rec_entity)

        # User's tracked Trades
        trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trades_orm:
            # Convert UserTrade ORM to RecommendationEntity structure for unified display
            try:
                # Ensure targets are list of dicts with Decimal for Targets VO
                targets_data = trade.targets or []
                targets_for_vo = [{'price': self._to_decimal(t.get('price')), 'close_percent': t.get('close_percent', 0.0)} for t in targets_data]

                trade_entity = RecommendationEntity(
                    id=trade.id,
                    asset=Symbol(trade.asset), side=Side(trade.side),
                    entry=Price(self._to_decimal(trade.entry)),
                    stop_loss=Price(self._to_decimal(trade.stop_loss)),
                    targets=Targets(targets_for_vo),
                    status=RecommendationStatusEntity.ACTIVE, # Map OPEN to ACTIVE
                    order_type=OrderType.MARKET, # Assume market for tracked trades
                    created_at=trade.created_at,
                    # Add other fields if needed, ensure type consistency
                    analyst_id=None, # Not an analyst rec
                    market=None, # Or fetch from source_recommendation if linked?
                    notes=None,
                    exit_strategy=ExitStrategy.MANUAL_CLOSE_ONLY, # User trades are manual
                )
                setattr(trade_entity, 'is_user_trade', True)
                open_positions.append(trade_entity)
            except Exception as conv_err:
                 logger.error(f"Failed to convert UserTrade {trade.id} to entity structure: {conv_err}", exc_info=True)


        open_positions.sort(key=lambda p: getattr(p, "created_at", datetime.min), reverse=True)
        return open_positions


    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """Returns detailed RecommendationEntity, enforcing ownership."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: return None

        if position_type == 'rec':
            rec_orm = self.repo.get(db_session, position_id) # Fetches with analyst + events
            # Allow viewing if analyst is the owner OR if trader is tracking this rec?
            # Current logic: Only owner analyst can view 'rec' details this way.
            if not rec_orm or rec_orm.analyst_id != user.id:
                # TODO: Add logic for traders viewing recs they track?
                return None
            if rec_entity := self.repo._to_entity(rec_orm):
                setattr(rec_entity, 'is_user_trade', False)
                return rec_entity
            else:
                 logger.error(f"Failed to convert owned Rec ORM {position_id} to entity.")
                 return None

        elif position_type == 'trade':
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if not trade_orm or trade_orm.user_id != user.id: # Strict ownership check
                return None
            try:
                # Convert UserTrade ORM to RecommendationEntity structure
                targets_data = trade_orm.targets or []
                targets_for_vo = [{'price': self._to_decimal(t.get('price')), 'close_percent': t.get('close_percent', 0.0)} for t in targets_data]

                trade_entity = RecommendationEntity(
                    id=trade_orm.id,
                    asset=Symbol(trade_orm.asset), side=Side(trade_orm.side),
                    entry=Price(self._to_decimal(trade_orm.entry)),
                    stop_loss=Price(self._to_decimal(trade_orm.stop_loss)),
                    targets=Targets(targets_for_vo),
                    status=RecommendationStatusEntity.ACTIVE if trade_orm.status == UserTradeStatus.OPEN else RecommendationStatusEntity.CLOSED,
                    order_type=OrderType.MARKET, # Assumption
                    created_at=trade_orm.created_at,
                    closed_at=trade_orm.closed_at,
                    exit_price=float(trade_orm.close_price) if trade_orm.close_price is not None else None,
                    exit_strategy=ExitStrategy.MANUAL_CLOSE_ONLY,
                )
                setattr(trade_entity, 'is_user_trade', True)
                # Add pnl if closed
                if trade_orm.pnl_percentage is not None:
                     setattr(trade_entity, 'final_pnl_percentage', float(trade_orm.pnl_percentage))
                return trade_entity
            except Exception as conv_err:
                 logger.error(f"Failed to convert UserTrade {trade_orm.id} to entity structure for details view: {conv_err}", exc_info=True)
                 return None
        else:
            logger.warning(f"Unknown position_type '{position_type}' requested.")
            return None

    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """Returns recent assets user interacted with, with fallbacks."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user: return []

        assets = set()
        if user.user_type == UserTypeEntity.ANALYST:
            # Get assets from recent analyst recommendations (open or recently closed)
            recs = db_session.query(Recommendation.asset)\
                .filter(Recommendation.analyst_id == user.id)\
                .order_by(Recommendation.created_at.desc())\
                .limit(limit * 2)\
                .distinct().all()
            assets.update(r.asset for r in recs)
        else: # TRADER
            # Get assets from recent user trades (open or recently closed)
            trades = db_session.query(UserTrade.asset)\
                .filter(UserTrade.user_id == user.id)\
                .order_by(UserTrade.created_at.desc())\
                .limit(limit * 2)\
                .distinct().all()
            assets.update(t.asset for t in trades)

        asset_list = list(assets)[:limit]

        # Add defaults if list is too short
        if len(asset_list) < limit:
            default_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
            for a in default_assets:
                if a not in asset_list and len(asset_list) < limit:
                    asset_list.append(a)
        return asset_list

# --- END of TradeService ---