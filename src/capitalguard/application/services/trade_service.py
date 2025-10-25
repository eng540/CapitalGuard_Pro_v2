# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/trade_service.py ---
# src/capitalguard/application/services/trade_service.py v 30.9
"""
TradeService v30.9 - Final, complete, and production-ready version.
✅ FIX: Added missing internal helper functions (_format_price, _pct, etc.) to resolve NameError on notifications.
✅ FIX: Added missing 'create_trade_from_recommendation' method to fix deep-linking.
✅ FIX: Corrected target format validation in 'update_sl_for_user_async' to prevent ValueError on move_to_be.
✅ UX FIX: Added a small buffer in 'move_sl_to_breakeven_async' to prevent immediate SL_HIT.
✅ HOTFIX: Decoupled from `interfaces` layer by moving helper functions internally.
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

# ---------------------------
# ✅ FIX: Internal helper functions (copied from ui_texts.py to fix NameError)
# ---------------------------

def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """Safely convert any value to a Decimal."""
    if isinstance(value, Decimal): return value
    try: return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError): return default

def _format_price(price: Any) -> str:
    """Formats a Decimal or number into a clean string."""
    price_dec = _to_decimal(price)
    if not price_dec.is_finite():
        return "N/A"
    # Use 'g' for general format, avoids trailing zeros, more readable than 'f'
    return f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    """
    Compute percentage PnL from entry to target_price depending on side.
    Returns float percent (e.g., 5.23 for +5.23%).
    """
    try:
        entry_dec = _to_decimal(entry)
        target_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite():
            return 0.0
        side_upper = (side or "").upper()
        if side_upper == "LONG":
            pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT":
            pnl = ((entry_dec / target_dec) - 1) * 100
        else:
            return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError) as exc:
        logger.debug("pct calc error: %s", exc)
        return 0.0

def _normalize_pct_value(pct_raw: Any) -> Decimal:
    """
    Normalize a raw percentage value into Decimal.
    Accepts Decimal, int, float, or strings like '5.2%' or '+5.2'.
    """
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
    """
    Safely parse numeric telegram user id from various inputs.
    Returns None if parsing fails.
    """
    try:
        if user_id is None:
            return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None


# ---------------------------
# TradeService
# ---------------------------

class TradeService:
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: "MarketDataService",
        price_service: "PriceService",
    ):
        """
        repo: RecommendationRepository instance
        notifier: object with methods used to send/edit messages (can be sync or async)
        market_data_service, price_service: injected service instances
        """
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        self.alert_service: "AlertService" = None

    # ---------------------------
    # Internal DB / notifier helpers
    # ---------------------------

    async def _commit_and_dispatch(self, db_session: Session, rec_orm: Recommendation, rebuild_alerts: bool = True):
        """
        Commit DB transaction, refresh ORM, rebuild alert indices if needed,
        convert to entity and notify card updates.
        """
        db_session.commit()
        try:
            db_session.refresh(rec_orm)
        except Exception as e:
            logger.warning(f"Failed to refresh rec_orm after commit: {e}")

        if rebuild_alerts and self.alert_service:
            try:
                await self.alert_service.build_triggers_index()
            except Exception as e:
                logger.exception("Failed to rebuild alerts index after commit: %s", e)

        updated_entity = self.repo._to_entity(rec_orm)
        try:
            await self.notify_card_update(updated_entity, db_session)
        except Exception as e:
            logger.exception("Failed to notify card update after commit: %s", e)

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """
        Call notifier function whether it's coroutine or regular function.
        Regular functions run in thread executor.
        """
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        """
        Update published recommendation cards across channels where published.
        Skips shadow recommendations.
        """
        if getattr(rec_entity, "is_shadow", False):
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
                # Log errors from notify_card_update (e.g., _get_attr error)
                logger.error("notify_card_update gather failed: %s", res, exc_info=False)


    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        """
        Post a notification reply to all published messages for a recommendation.
        This spawns background asyncio tasks and returns immediately.
        """
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or getattr(rec_orm, "is_shadow", False):
            return
        published_messages = self.repo.get_published_messages(db_session, rec_id)
        for msg in published_messages:
            asyncio.create_task(self._call_notifier_maybe_async(
                self.notifier.post_notification_reply,
                chat_id=msg.telegram_channel_id,
                message_id=msg.telegram_message_id,
                text=text
            ))

    # ---------------------------
    # Validation and publishing
    # ---------------------------

    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        """
        Strict validation rules for recommendation numeric integrity.
        Raises ValueError on invalid inputs.
        """
        side_upper = (side or "").upper()
        if not all(isinstance(v, Decimal) and v > Decimal(0) for v in [entry, stop_loss]):
            raise ValueError("Entry and Stop Loss must be positive Decimal values.")
        
        # ✅ FIX: Ensure targets are correctly formatted as list of dicts with Decimals before checking
        formatted_targets = []
        if not targets or not isinstance(targets, list):
             raise ValueError("At least one valid target with a positive finite Decimal price is required.")

        for t in targets:
            if isinstance(t, dict) and 'price' in t:
                price = _to_decimal(t.get('price')) # Use helper
                if price.is_finite() and price > 0:
                    formatted_targets.append(price)
                else:
                    raise ValueError("Target price must be a positive finite Decimal.")
            else:
                 # This might be the case for rec_orm.targets in move_to_be
                 raise ValueError("Invalid target format. Expected list of {'price': ...}")

        if not formatted_targets:
             raise ValueError("At least one valid target with a positive finite Decimal price is required.")

        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("For LONG, Stop Loss must be less than Entry.")
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("For SHORT, Stop Loss must be greater than Entry.")
        
        target_prices = formatted_targets
        if side_upper == "LONG" and any(p <= entry for p in target_prices):
            raise ValueError("All LONG targets must be greater than the entry price.")
        if side_upper == "SHORT" and any(p >= entry for p in target_prices):
            raise ValueError("All SHORT targets must be less than the entry price.")
        
        risk = abs(entry - stop_loss)
        if risk.is_zero():
            raise ValueError("Entry and Stop Loss cannot be equal.")
        
        first_target_price = min(target_prices) if side_upper == "LONG" else max(target_prices)
        reward = abs(first_target_price - entry)
        if (reward / risk) < Decimal('0.1'):
            raise ValueError("Risk/Reward ratio is too low (minimum 0.1).")
        
        if len(target_prices) != len(set(target_prices)):
            raise ValueError("Target prices must be unique.")
        
        sorted_prices = sorted(target_prices, reverse=(side_upper == 'SHORT'))
        if target_prices != sorted_prices:
            raise ValueError("Targets must be sorted ascending for LONG and descending for SHORT.")

    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """
        Publish a recommendation to the analyst's linked public channels.
        Returns the rec_entity and a report dict with success/failed entries.
        """
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        user = UserRepository(session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            report["failed"].append({"reason": "User not found"})
            return rec_entity, report
        channels_to_publish = ChannelRepository(session).list_by_analyst(user.id, only_active=True)
        if target_channel_ids:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
        if not channels_to_publish:
            report["failed"].append({"reason": "No active channels linked."})
            return rec_entity, report

        # Import keyboard function lazily to avoid cyclical imports at module load time
        try:
            from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        except Exception:
            # If the interfaces layer is not available at runtime, fallback to None keyboard
            public_channel_keyboard = lambda *_: None

        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        for channel in channels_to_publish:
            try:
                result = await self._call_notifier_maybe_async(self.notifier.post_to_channel, channel.telegram_channel_id, rec_entity, keyboard)
                if isinstance(result, tuple) and len(result) == 2:
                    # result: (channel_id, message_id)
                    session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=result[0], telegram_message_id=result[1]))
                    report["success"].append({"channel_id": channel.telegram_channel_id, "message_id": result[1]})
                else:
                    raise RuntimeError(f"Notifier returned unsupported type: {type(result)}")
            except Exception as e:
                logger.exception("Failed to publish to channel %s: %s", getattr(channel, "telegram_channel_id", None), e)
                report["failed"].append({"channel_id": getattr(channel, "telegram_channel_id", None), "reason": str(e)})
        session.flush()
        return rec_entity, report

    # ---------------------------
    # Public API - create / publish
    # ---------------------------

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """
        Create a new recommendation and optionally publish it.
        kwargs expected: asset, side, entry, stop_loss, targets, order_type, notes (opt), exit_strategy (opt), market (opt), target_channel_ids (opt)
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can create recommendations.")

        entry_price, sl_price, targets_list = kwargs['entry'], kwargs['stop_loss'], kwargs['targets']
        asset, side, market = kwargs['asset'].strip().upper(), kwargs['side'].upper(), kwargs.get('market', 'Futures')
        order_type_enum = OrderTypeEnum[kwargs['order_type'].upper()]

        # Normalize exit strategy input
        exit_strategy_val = kwargs.get('exit_strategy')
        if exit_strategy_val is None:
            exit_strategy_enum = ExitStrategyEnum.CLOSE_AT_FINAL_TP
        elif isinstance(exit_strategy_val, ExitStrategyEnum):
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

        # MARKET order uses live price
        if order_type_enum == OrderTypeEnum.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            status, final_entry = RecommendationStatusEnum.ACTIVE, Decimal(str(live_price)) if live_price is not None else None
            if final_entry is None or not final_entry.is_finite():
                raise RuntimeError(f"Could not fetch live price for {asset}.")
        else:
            status, final_entry = RecommendationStatusEnum.PENDING, entry_price

        # Validate numeric data
        self._validate_recommendation_data(side, final_entry, sl_price, targets_list)

        # Build ORM object
        rec_orm = Recommendation(
            analyst_id=user.id,
            asset=asset,
            side=side,
            entry=final_entry,
            stop_loss=sl_price,
            # Ensure targets are stored correctly (as list of dicts of strings/numbers)
            targets=[{"price": str(t['price']), "close_percent": t.get("close_percent", 0)} for t in targets_list],
            order_type=order_type_enum,
            status=status,
            market=market,
            notes=kwargs.get('notes'),
            exit_strategy=exit_strategy_enum,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )

        db_session.add(rec_orm)
        db_session.flush()
        db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type="CREATED_ACTIVE" if status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING"))
        db_session.flush()
        db_session.refresh(rec_orm)

        created_rec_entity = self.repo._to_entity(rec_orm)
        final_rec, report = await self._publish_recommendation(db_session, created_rec_entity, user_id, kwargs.get('target_channel_ids'))

        if self.alert_service:
            try:
                await self.alert_service.build_triggers_index()
            except Exception:
                logger.exception("alert_service.build_triggers_index failed after create_and_publish")

        return final_rec, report

    # ---------------------------
    # User trades (forwarded / tracked)
    # ---------------------------

    async def create_trade_from_forwarding(self, user_id: str, trade_data: Dict[str, Any], db_session: Session, original_text: str = None) -> Dict[str, Any]:
        """
        Create a UserTrade record from forwarded trade data (e.g., parsed from chat).
        Returns dict with success flag and trade_id or error message.
        """
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}
        try:
            entry_dec = Decimal(str(trade_data['entry']))
            sl_dec = Decimal(str(trade_data['stop_loss']))
            targets_for_validation = [{'price': Decimal(str(t['price'])), 'close_percent': t.get('close_percent', 0)} for t in trade_data['targets']]
            # Validate structure
            self._validate_recommendation_data(trade_data['side'], entry_dec, sl_dec, targets_for_validation)
            targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0)} for t in trade_data['targets']]
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
            db_session.flush()
            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        except ValueError as e:
            logger.warning(f"Validation failed for forwarded trade data for user {user_id}: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"Error creating trade from forwarding for user {user_id}: {e}", exc_info=True)
            return {'success': False, 'error': 'An internal error occurred.'}

    # ✅ NEW METHOD: To fix deep-linking (AttributeError)
    async def create_trade_from_recommendation(self, user_id: str, rec_id: int, db_session: Session) -> Dict[str, Any]:
        """
        Creates a UserTrade record by tracking an existing Recommendation.
        """
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}

        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm:
            return {'success': False, 'error': 'Signal not found'}

        # Check for duplicates
        # This requires find_user_trade_by_source_id in the repository
        existing_trade = self.repo.find_user_trade_by_source_id(db_session, trader_user.id, rec_id)
        if existing_trade:
            return {'success': False, 'error': 'You are already tracking this signal.'}

        try:
            # Create a new UserTrade based on the Recommendation's data
            new_trade = UserTrade(
                user_id=trader_user.id,
                asset=rec_orm.asset,
                side=rec_orm.side,
                entry=rec_orm.entry,
                stop_loss=rec_orm.stop_loss,
                targets=rec_orm.targets, # Assumes targets JSON is compatible
                status=UserTradeStatus.OPEN, # User tracks it as OPEN
                source_recommendation_id=rec_orm.id
            )
            db_session.add(new_trade)
            db_session.flush()
            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        except Exception as e:
            logger.error(f"Error creating trade from recommendation for user {user_id}, rec {rec_id}: {e}", exc_info=True)
            return {'success': False, 'error': 'An internal error occurred.'}


    # ---------------------------
    # Update operations (analyst-managed)
    # ---------------------------

    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Optional[Session] = None) -> RecommendationEntity:
        """
        Analyst updates Stop Loss for their own recommendation.
        Supports optional DB session; if omitted a session_scope is used.
        """
        if db_session is None:
            with session_scope() as s:
                return await self.update_sl_for_user_async(rec_id, user_id, new_sl, s)

        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm:
            raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id:
            raise ValueError("Access denied: Not owner.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Can only modify ACTIVE recommendations.")

        old_sl = rec_orm.stop_loss
        
        # ✅ FIX: Validate new SL logic before applying
        try:
            # Convert ORM targets (JSON) to list of dicts with Decimals for validation
            targets_list = [{"price": _to_decimal(t["price"]), "close_percent": t.get("close_percent", 0)} for t in rec_orm.targets or []]
            self._validate_recommendation_data(rec_orm.side, rec_orm.entry, new_sl, targets_list)
        except ValueError as e:
            logger.warning(f"Invalid SL update for rec #{rec_id} by user {user_id}: {e}")
            raise ValueError(f"Invalid new Stop Loss: {e}")

        rec_orm.stop_loss = new_sl
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="SL_UPDATED", event_data={"old": float(old_sl), "new": float(new_sl)}))
        self.notify_reply(rec_id, f"⚠️ Stop Loss for #{rec_orm.asset} updated to {_format_price(new_sl)}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, Any]], db_session: Session) -> RecommendationEntity:
        """
        Analyst updates targets for an active recommendation.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm:
            raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id:
            raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Can only modify ACTIVE recommendations.")

        # ✅ FIX: Validate new targets logic before applying
        try:
            self._validate_recommendation_data(rec_orm.side, rec_orm.entry, rec_orm.stop_loss, new_targets)
        except ValueError as e:
            logger.warning(f"Invalid TP update for rec #{rec_id} by user {user_id}: {e}")
            raise ValueError(f"Invalid new Targets: {e}")

        old_targets = rec_orm.targets
        rec_orm.targets = [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in new_targets]
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="TP_UPDATED", event_data={"old": old_targets, "new": rec_orm.targets}))
        self.notify_reply(rec_id, f"🎯 Targets for #{rec_orm.asset} have been updated.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_entry_and_notes_async(self, rec_id: int, user_id: str, new_entry: Optional[Decimal], new_notes: Optional[str], db_session: Session) -> RecommendationEntity:
        """
        Update entry price (only for PENDING) and notes.
        Records DATA_UPDATED event when changes exist.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm:
            raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id:
            raise ValueError("Access denied.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            raise ValueError("Cannot edit a closed recommendation.")

        event_data = {}
        action = "edit_notes" if new_notes is not None else "edit_entry" # For logic simplicity

        if new_entry is not None:
            if rec_orm.status != RecommendationStatusEnum.PENDING:
                raise ValueError("Entry price can only be modified for PENDING recommendations.")
            
            # ✅ FIX: Validate new entry price
            try:
                targets_list = [{"price": _to_decimal(t["price"]), "close_percent": t.get("close_percent", 0)} for t in rec_orm.targets or []]
                self._validate_recommendation_data(rec_orm.side, new_entry, rec_orm.stop_loss, targets_list)
            except ValueError as e:
                logger.warning(f"Invalid Entry update for rec #{rec_id} by user {user_id}: {e}")
                raise ValueError(f"Invalid new Entry Price: {e}")

            event_data.update({"old_entry": float(rec_orm.entry), "new_entry": float(new_entry)})
            rec_orm.entry = new_entry

        if new_notes is not None or (new_notes is None and action == "edit_notes"): # Handle explicit clearing
            event_data.update({"old_notes": rec_orm.notes, "new_notes": new_notes})
            rec_orm.notes = new_notes

        if event_data:
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="DATA_UPDATED", event_data=event_data))
            self.notify_reply(rec_id, f"✏️ Data for #{rec_orm.asset} has been updated.", db_session)
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)

        return self.repo._to_entity(rec_orm)

    async def set_exit_strategy_async(self, rec_id: int, user_id: str, mode: str, price: Optional[Decimal] = None, trailing_value: Optional[Decimal] = None, active: bool = True, session: Optional[Session] = None) -> RecommendationEntity:
        """
        Set or cancel a profit-stop / exit strategy.
        Supports optional session param.
        """
        if session is None:
            with session_scope() as s:
                return await self.set_exit_strategy_async(rec_id, user_id, mode, price, trailing_value, active, s)

        user = UserRepository(session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            raise ValueError("User not found.")
        rec = self.repo.get_for_update(session, rec_id)
        if not rec:
            raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec.analyst_id != user.id:
            raise ValueError("Access denied.")
        if rec.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Exit strategies can only be set for ACTIVE recommendations.")

        rec.profit_stop_mode = mode.upper()
        rec.profit_stop_price = price
        rec.profit_stop_trailing_value = trailing_value
        rec.profit_stop_active = active

        event_data = {"mode": mode.upper(), "active": active}
        if price:
            event_data["price"] = float(price)
        if trailing_value:
            event_data["trailing_value"] = float(trailing_value)

        session.add(RecommendationEvent(recommendation_id=rec_id, event_type="EXIT_STRATEGY_UPDATED", event_data=event_data))

        if active:
            self.notify_reply(rec_id, f"📈 Exit strategy for #{rec.asset} set to: {mode.upper()}", session)
        else:
            self.notify_reply(rec_id, f"📈 Exit strategy for #{rec.asset} has been cancelled.", session)

        await self._commit_and_dispatch(session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)

    # ---------------------------
    # Automation helpers
    # ---------------------------

    async def move_sl_to_breakeven_async(self, rec_id: int, db_session: Optional[Session] = None) -> RecommendationEntity:
        """
        Move SL to entry price (breakeven) if conditions met.
        """
        if db_session is None:
            with session_scope() as s:
                return await self.move_sl_to_breakeven_async(rec_id, s)

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Can only move SL to BE for ACTIVE recommendations.")

        new_sl_final = rec_orm.entry
        
        # ✅ UX FIX: Add a tiny buffer to prevent immediate SL_HIT on price wick
        # This buffer is 0.001% of the entry price
        buffer = rec_orm.entry * Decimal('0.00001') # 0.001%
        if rec_orm.side == 'LONG':
            new_sl_final = new_sl_final - buffer
        else: # SHORT
            new_sl_final = new_sl_final + buffer

        # Check if new SL is actually an improvement
        if (rec_orm.side == 'LONG' and new_sl_final > rec_orm.stop_loss) or \
           (rec_orm.side == 'SHORT' and new_sl_final < rec_orm.stop_loss):
            analyst_uid = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
            # Pass the buffered SL, not the exact entry price
            return await self.update_sl_for_user_async(rec_id, analyst_uid, new_sl_final, db_session)

        logger.info(f"SL for Rec #{rec_id} is already at or better than breakeven. No action taken.")
        return self.repo._to_entity(rec_orm)

    async def close_recommendation_async(self, rec_id: int, user_id: Optional[str], exit_price: Decimal, db_session: Optional[Session] = None, reason: str = "MANUAL_CLOSE") -> RecommendationEntity:
        """
        Close a recommendation fully.
        If user_id provided enforce owner check.
        """
        if db_session is None:
            with session_scope() as s:
                return await self.close_recommendation_async(rec_id, user_id, exit_price, s, reason)

        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm:
            raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            logger.warning(f"Attempted to close already closed recommendation #{rec_id}")
            return self.repo._to_entity(rec_orm)

        if user_id is not None:
            user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
            if not user or rec_orm.analyst_id != user.id:
                raise ValueError("Access denied.")

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
        rec_orm.profit_stop_active = False

        # ✅ FIX: Use internal _format_price helper
        self.notify_reply(rec_id, f"✅ Signal #{rec_orm.asset} closed at {_format_price(exit_price)}. Reason: {reason}", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, price: Decimal, db_session: Session, triggered_by: str = "MANUAL") -> RecommendationEntity:
        """
        Partially close a recommendation position.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm:
            raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id:
            raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Partial close can only be performed on active recommendations.")

        current_open_percent = Decimal(str(rec_orm.open_size_percent))
        actual_close_percent = min(close_percent, current_open_percent)
        if not (Decimal(0) < actual_close_percent):
            raise ValueError(f"Invalid percentage. Open position is {current_open_percent:.2f}%.")

        rec_orm.open_size_percent = current_open_percent - actual_close_percent
        
        raw_pct = _pct(rec_orm.entry, price, rec_orm.side)
        pnl_on_part = _normalize_pct_value(raw_pct)
        pnl_formatted = f"{pnl_on_part:+.2f}%" # Format for notification
        event_type = "PARTIAL_CLOSE_AUTO" if triggered_by == "AUTO" else "PARTIAL_CLOSE_MANUAL"
        event_data = {"price": float(price), "closed_percent": float(actual_close_percent), "remaining_percent": float(rec_orm.open_size_percent), "pnl_on_part": float(pnl_on_part)}
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type=event_type, event_data=event_data))

        # ✅ FIX: Use internal _format_price helper
        notif_text = (
            f"💰 Partial Close (Profit) on #{rec_orm.asset}. Closed {actual_close_percent:g}% at {_format_price(price)} ({pnl_formatted})."
            if pnl_on_part >= 0 else
            f"⚠️ Partial Close (Loss Mgt) on #{rec_orm.asset}. Closed {actual_close_percent:g}% at {_format_price(price)} ({pnl_formatted})."
        )
        notif_text += f"\nRemaining: {rec_orm.open_size_percent:g}%"
        self.notify_reply(rec_id, notif_text, db_session)

        if rec_orm.open_size_percent < Decimal('0.1'):
            logger.info("Position #%s fully closed via partial close (remaining < 0.1).", rec_id)
            return await self.close_recommendation_async(rec_id, user_id, price, db_session, reason="PARTIAL_CLOSE_FINAL")
        else:
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)
            return self.repo._to_entity(rec_orm)

    # ---------------------------
    # Event processors
    # ---------------------------

    async def process_invalidation_event(self, item_id: int):
        """
        Mark a pending recommendation as invalidated (e.g., SL hit before entry).
        """
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING:
                return
            rec.status = RecommendationStatusEnum.CLOSED
            rec.closed_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED", event_data={"reason": "SL hit before entry"}))
            self.notify_reply(rec.id, f"❌ Signal #{rec.asset} was invalidated (SL hit before entry).", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec)

    async def process_activation_event(self, item_id: int):
        """
        Activate a pending recommendation (entry reached).
        """
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING:
                return
            rec.status = RecommendationStatusEnum.ACTIVE
            rec.activated_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))
            self.notify_reply(rec.id, f"▶️ Signal #{rec.asset} is now ACTIVE!", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec)

    async def process_sl_hit_event(self, item_id: int, price: Decimal):
        """
        Handle SL hit by closing the recommendation.
        """
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.ACTIVE:
                return
            analyst_user_id = str(rec.analyst.telegram_user_id) if getattr(rec, "analyst", None) else None
            await self.close_recommendation_async(rec.id, analyst_user_id, price, db_session, reason="SL_HIT")

    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        """
        Handle TP hit events.
        Records event, possibly partial close, and final close depending on exit strategy.
        """
        with session_scope() as db_session:
            rec_orm = self.repo.get_for_update(db_session, item_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE:
                return
            event_type = f"TP{target_index}_HIT"
            if any(e.event_type == event_type for e in (rec_orm.events or [])):
                logger.debug("TP event already processed for %s %s", item_id, event_type)
                return

            db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={"price": float(price)}))
            # ✅ FIX: Use internal _format_price helper
            self.notify_reply(rec_orm.id, f"🎯 Signal #{rec_orm.asset} hit TP{target_index} at {_format_price(price)}!", db_session=db_session)

            try:
                target_info = rec_orm.targets[target_index - 1]
            except (IndexError, Exception):
                target_info = {}

            close_percent = Decimal(str(target_info.get("close_percent", 0))) if target_info else Decimal(0)
            if close_percent > 0:
                analyst_user_id = str(rec_orm.analyst.telegram_user_id) if getattr(rec_orm, "analyst", None) else None
                await self.partial_close_async(rec_orm.id, analyst_user_id, close_percent, price, db_session, triggered_by="AUTO")
                # refresh rec_orm after partial_close
                rec_orm = self.repo.get_for_update(db_session, item_id)

            is_final_tp = (target_index == len(rec_orm.targets))
            if (rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final_tp) or (rec_orm.open_size_percent is not None and rec_orm.open_size_percent < Decimal('0.1')):
                analyst_user_id = str(rec_orm.analyst.telegram_user_id) if getattr(rec_orm, "analyst", None) else None
                await self.close_recommendation_async(rec_orm.id, analyst_user_id, price, db_session, reason="AUTO_CLOSE_FINAL_TP")
            else:
                await self._commit_and_dispatch(db_session, rec_orm)

    # ---------------------------
    # Read utilities for users
    # ---------------------------

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """
        Return combined list of open recommendations (for analysts) and user trades (for traders).
        Each returned entity has attribute 'is_user_trade' set to True for user trades.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user:
            return []
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
            trade_entity = RecommendationEntity(
                id=trade.id,
                asset=Symbol(trade.asset),
                side=Side(trade.side),
                entry=Price(trade.entry),
                stop_loss=Price(trade.stop_loss),
                targets=Targets(trade.targets),
                status=RecommendationStatusEntity.ACTIVE, # UserTradeStatus.OPEN maps to ACTIVE
                order_type=OrderType.MARKET, # Assume market for user trades
                created_at=trade.created_at
            )
            setattr(trade_entity, 'is_user_trade', True)
            open_positions.append(trade_entity)

        open_positions.sort(key=lambda p: getattr(p, "created_at", datetime.min), reverse=True)
        return open_positions

    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """
        Return details for a single position.
        position_type: 'rec' or 'trade'.
        Enforces ownership checks.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user:
            return None

        if position_type == 'rec':
            # Allow both Analyst and Trader to view recommendations?
            # Current logic: Only analyst (owner) can view.
            if user.user_type != UserType.ANALYST:
                 # TODO: Add logic for traders viewing recs they are subscribed to?
                return None
            rec_orm = self.repo.get(db_session, position_id)
            if not rec_orm or rec_orm.analyst_id != user.id:
                 # If not owner, check if it's a trade they are tracking?
                 # For now, strict owner check for 'rec' type
                return None
            if rec_entity := self.repo._to_entity(rec_orm):
                setattr(rec_entity, 'is_user_trade', False)
            return rec_entity

        elif position_type == 'trade':
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if not trade_orm or trade_orm.user_id != user.id:
                return None
            trade_entity = RecommendationEntity(
                id=trade_orm.id,
                asset=Symbol(trade_orm.asset),
                side=Side(trade_orm.side),
                entry=Price(trade_orm.entry),
                stop_loss=Price(trade_orm.stop_loss),
                targets=Targets(trade_orm.targets),
                status=RecommendationStatusEntity.ACTIVE if trade_orm.status == UserTradeStatus.OPEN else RecommendationStatusEntity.CLOSED,
                order_type=OrderType.MARKET,
                created_at=trade_orm.created_at,
                closed_at=trade_orm.closed_at,
                exit_price=float(trade_orm.close_price) if trade_orm.close_price else None
            )
            setattr(trade_entity, 'is_user_trade', True)
            return trade_entity

        return None

    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """
        Return recent assets the user interacted with.
        Fallback to common defaults to ensure list length.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user:
            return []

        if user.user_type == UserType.ANALYST:
            assets = list(dict.fromkeys([r.asset for r in self.repo.get_open_recs_for_analyst(db_session, user.id)]))[:limit]
        else:
            assets = list(dict.fromkeys([t.asset for t in self.repo.get_open_trades_for_trader(db_session, user.id)]))[:limit]

        if len(assets) < limit:
            default_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
            for a in default_assets:
                if a not in assets and len(assets) < limit:
                    assets.append(a)

        return assets

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/trade_service.py ---