"""
File: src/capitalguard/application/services/trade_service.py
Version: v29.6 (Production Ready & Final, merged with v29.4 completeness)
✅ THE FIX: Restored all v29.4 methods and added create_trade_from_forwarding to resolve AttributeError.
Reviewed-by: Guardian Protocol
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
    OrderType,
    ExitStrategy,
    UserType
)
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets
from capitalguard.interfaces.telegram.ui_texts import _pct

if False:
    # type-only imports for runtime optional services
    from .alert_service import AlertService
    from .price_service import PriceService
    from .market_data_service import MarketDataService

logger = logging.getLogger(__name__)


def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """Safely parse telegram user id (string) to integer or return None."""
    try:
        if user_id is None:
            return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None


def _normalize_pct_value(pct_raw: Any) -> Decimal:
    """
    Normalize the output of ui_texts._pct to a Decimal for numeric comparisons.
    Accepts numbers, Decimal, or formatted strings like '+1.23%' or '1.23'.
    Falls back to Decimal(0) on parse failure while logging a warning.
    """
    try:
        if isinstance(pct_raw, Decimal):
            return pct_raw
        if isinstance(pct_raw, (int, float)):
            return Decimal(str(pct_raw))
        if isinstance(pct_raw, str):
            # remove percent sign and plus
            s = pct_raw.strip()
            s = s.replace('%', '')
            s = s.replace('+', '')
            s = s.replace(',', '')  # thousand separators if any
            return Decimal(s)
        # unknown type
        return Decimal(str(pct_raw))
    except (InvalidOperation, Exception) as exc:
        logger.warning("Unable to normalize pct value '%s' (%s); defaulting to 0", pct_raw, exc)
        return Decimal(0)


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
        self.alert_service: "AlertService" = None  # injected after construction if available

    # -------------------
    # Core utilities
    # -------------------
    async def _commit_and_dispatch(self, db_session: Session, rec_orm: Recommendation, rebuild_alerts: bool = True):
        """
        The single source of truth for committing recommendation changes and dispatching updates.
        """
        db_session.commit()
        try:
            db_session.refresh(rec_orm, ['events', 'analyst'])
        except Exception:
            try:
                db_session.refresh(rec_orm)
            except Exception as e:
                logger.exception("Failed to refresh rec_orm: %s", e)

        if rebuild_alerts and self.alert_service:
            try:
                await self.alert_service.build_triggers_index()
            except Exception as e:
                logger.exception("Failed to rebuild alerts index: %s", e)

        updated_entity = self.repo._to_entity(rec_orm)
        try:
            await self.notify_card_update(updated_entity, db_session)
        except Exception as e:
            logger.exception("Failed to notify card update: %s", e)

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """Call notifier function, work whether it's sync or async."""
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        """Edit the recommendation cards previously published to channels/messages."""
        if getattr(rec_entity, "is_shadow", False):
            return
        published_messages = self.repo.get_published_messages(db_session, rec_entity.id)
        if not published_messages:
            return
        tasks = []
        for msg_meta in published_messages:
            try:
                tasks.append(self._call_notifier_maybe_async(
                    self.notifier.edit_recommendation_card_by_ids,
                    channel_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    rec=rec_entity
                ))
            except Exception as e:
                logger.exception("Notifier invocation failed for edit_recommendation_card_by_ids: %s", e)
        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        for res in results:
            if isinstance(res, Exception):
                logger.error("notify_card_update failed: %s", res)

    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        """Post a reply/notification to all published messages of a recommendation."""
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or getattr(rec_orm, "is_shadow", False):
            return
        published_messages = self.repo.get_published_messages(db_session, rec_id)
        for msg_meta in published_messages:
            # schedule notification without awaiting
            asyncio.create_task(self._call_notifier_maybe_async(
                self.notifier.post_notification_reply,
                chat_id=msg_meta.telegram_channel_id,
                message_id=msg_meta.telegram_message_id,
                text=text
            ))

    # -------------------
    # Validation
    # -------------------
    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        """
        Validate core numeric and directional consistency for recommendations.
        Ensures:
         - positive numbers
         - SL direction consistent with LONG/SHORT
         - target prices consistent and ordered
         - reasonable reward:risk
        """
        side_upper = (side or "").upper()
        if not all(isinstance(v, Decimal) and v > Decimal(0) for v in [entry, stop_loss]):
            raise ValueError("Entry and Stop Loss must be positive Decimal values.")
        if not targets or not all(isinstance(t.get('price'), Decimal) and t.get('price') > Decimal(0) for t in targets):
            raise ValueError("At least one valid target with Decimal price is required.")
        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("For LONG, Stop Loss must be less than Entry.")
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("For SHORT, Stop Loss must be greater than Entry.")

        target_prices = [t['price'] for t in targets]
        if side_upper == "LONG" and any(p <= entry for p in target_prices):
            raise ValueError("All LONG targets must be greater than entry price.")
        if side_upper == "SHORT" and any(p >= entry for p in target_prices):
            raise ValueError("All SHORT targets must be less than entry price.")

        risk = abs(entry - stop_loss)
        if risk == 0:
            raise ValueError("Entry and Stop Loss cannot be equal.")
        first_target_price = min(target_prices) if side_upper == "LONG" else max(target_prices)
        reward = abs(first_target_price - entry)
        if (reward / risk) < Decimal('0.1'):
            raise ValueError("Risk/Reward ratio is too low (min 0.1).")

        # uniqueness & ordering
        if len(target_prices) != len(set(target_prices)):
            raise ValueError("Target prices must be unique.")
        sorted_prices = sorted(target_prices, reverse=(side_upper == 'SHORT'))
        if target_prices != sorted_prices:
            raise ValueError("Targets must be sorted ascending for LONG and descending for SHORT.")

    # -------------------
    # Publishing & creation
    # -------------------
    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """
        Publish created recommendation to analyst channels.
        Returns (entity, report) where report includes success/failed per channel.
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

        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        for channel in channels_to_publish:
            try:
                result = await self._call_notifier_maybe_async(self.notifier.post_to_channel, channel.telegram_channel_id, rec_entity, keyboard)
                if isinstance(result, tuple) and len(result) == 2:
                    session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=result[0], telegram_message_id=result[1]))
                    report["success"].append({"channel_id": channel.telegram_channel_id, "message_id": result[1]})
                else:
                    raise RuntimeError(f"Notifier returned unsupported type: {type(result)}")
            except Exception as e:
                logger.exception("Failed to publish to channel %s: %s", getattr(channel, "telegram_channel_id", None), e)
                report["failed"].append({"channel_id": getattr(channel, "telegram_channel_id", None), "reason": str(e)})
        session.flush()
        return rec_entity, report

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """
        Create recommendation record, validate, optionally activate immediately
        (for MARKET order type), then publish to channels.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user or user.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can create recommendations.")

        entry_price, sl_price = kwargs['entry'], kwargs['stop_loss']
        targets_list = kwargs['targets']
        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        order_type_enum = OrderTypeEnum[kwargs['order_type'].upper()]

        # [FIX] Normalize incoming exit_strategy to prevent type/key errors
        exit_strategy_val = kwargs.get('exit_strategy')
        if exit_strategy_val is None:
            exit_strategy_enum = ExitStrategyEnum.CLOSE_AT_FINAL_TP
        elif isinstance(exit_strategy_val, ExitStrategyEnum):
            exit_strategy_enum = exit_strategy_val
        elif isinstance(exit_strategy_val, ExitStrategy):
            exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.name]
        elif isinstance(exit_strategy_val, str):
            try:
                # Attempt to match by name (e.g., "CLOSE_AT_FINAL_TP")
                exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.upper()]
            except KeyError:
                raise ValueError(f"Unsupported exit_strategy string value: {exit_strategy_val}")
        else:
            raise ValueError(f"Unsupported exit_strategy format: {type(exit_strategy_val)}")

        # Determine initial status and entry price
        if order_type_enum == OrderTypeEnum.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            status = RecommendationStatusEnum.ACTIVE
            final_entry = Decimal(str(live_price)) if live_price is not None else None
            if final_entry is None or not final_entry.is_finite():
                raise RuntimeError(f"Could not fetch live price for {asset}.")
        else:
            status = RecommendationStatusEnum.PENDING
            final_entry = entry_price

        self._validate_recommendation_data(side, final_entry, sl_price, targets_list)

        rec_orm = Recommendation(
            analyst_id=user.id,
            asset=asset,
            side=side,
            entry=final_entry,
            stop_loss=sl_price,
            targets=targets_list,
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

    # -------------------
    # Trades / User trades
    # -------------------
    async def create_trade_from_recommendation(self, user_id: str, rec_id: int, db_session: Session) -> Dict[str, Any]:
        """
        Create a UserTrade from an existing recommendation for a trader.
        Guards against duplicate open trades for same rec.
        """
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm:
            return {'success': False, 'error': 'Recommendation not found'}

        existing_trade = db_session.query(UserTrade).filter(
            UserTrade.user_id == trader_user.id,
            UserTrade.source_recommendation_id == rec_id,
            UserTrade.status == UserTradeStatus.OPEN
        ).first()
        if existing_trade:
            return {'success': False, 'error': 'You are already tracking this signal.'}

        new_trade = UserTrade(
            user_id=trader_user.id,
            source_recommendation_id=rec_orm.id,
            asset=rec_orm.asset,
            side=rec_orm.side,
            entry=rec_orm.entry,
            stop_loss=rec_orm.stop_loss,
            targets=rec_orm.targets,
            status=UserTradeStatus.OPEN
        )
        db_session.add(new_trade)
        db_session.flush()
        if self.alert_service:
            try:
                await self.alert_service.build_triggers_index()
            except Exception:
                logger.exception("Failed to rebuild alerts index after create_trade_from_recommendation")
        return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}

    # -------------------
    # ✅ NEW: Create trade from forwarded message (final fix)
    # -------------------
    async def create_trade_from_forwarding(self, user_id: str, trade_data: Dict[str, Any], db_session: Session, original_text: str = None) -> Dict[str, Any]:
        """
        Creates a UserTrade from data parsed from a forwarded message.
        """
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}

        try:
            # Convert floats/strings from parser to Decimals for DB and validation
            entry_dec = Decimal(str(trade_data['entry']))
            sl_dec = Decimal(str(trade_data['stop_loss']))
            targets_for_validation = [{'price': Decimal(str(t['price'])), 'close_percent': t.get('close_percent', 0)} for t in trade_data['targets']]
            
            # Use the same robust validation
            self._validate_recommendation_data(trade_data['side'], entry_dec, sl_dec, targets_for_validation)

            # Prepare targets for JSONB storage (strings are safer)
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
            
            if self.alert_service:
                try:
                    await self.alert_service.build_triggers_index()
                except Exception:
                    logger.exception("Failed to rebuild alerts index after create_trade_from_forwarding")

            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        except ValueError as e:
            logger.warning(f"Validation failed for forwarded trade data for user {user_id}: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"Error creating trade from forwarding for user {user_id}: {e}", exc_info=True)
            return {'success': False, 'error': 'An internal error occurred.'}

    # -------------------
    # Updates (SL / Targets / Strategy / Entry & Notes / Profit Stop)
    # -------------------
    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Session) -> RecommendationEntity:
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
        rec_orm.stop_loss = new_sl
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="SL_UPDATED", event_data={"old": float(old_sl), "new": float(new_sl)}))
        self.notify_reply(rec_id, f"⚠️ Stop Loss for #{rec_orm.asset} updated to {new_sl:g}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, Any]], db_session: Session) -> RecommendationEntity:
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

        old_targets = rec_orm.targets
        rec_orm.targets = [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in new_targets]
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="TP_UPDATED", event_data={"old": old_targets, "new": rec_orm.targets}))
        self.notify_reply(rec_id, f"🎯 Targets for #{rec_orm.asset} have been updated.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_exit_strategy_async(self, rec_id: int, user_id: str, new_strategy: ExitStrategy, db_session: Session) -> RecommendationEntity:
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

        old_strategy = rec_orm.exit_strategy
        rec_orm.exit_strategy = ExitStrategyEnum[new_strategy.name]
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="STRATEGY_UPDATED", event_data={"old": old_strategy.value, "new": new_strategy.value}))
        strategy_text = "Auto-close at final TP" if new_strategy == ExitStrategy.CLOSE_AT_FINAL_TP else "Manual close only"
        self.notify_reply(rec_id, f"📈 Exit strategy for #{rec_orm.asset} updated to: {strategy_text}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_entry_and_notes_async(self, rec_id: int, user_id: str, new_entry: Decimal, new_notes: Optional[str], db_session: Session) -> RecommendationEntity:
        """
        Edit entry price and notes for an analyst's recommendation.
        This is intentionally non-invasive: it updates the fields, logs an event, and dispatches notifications.
        Validation: requires ACTIVE recommendations (to avoid editing historical closed ones).
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm:
            raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id:
            raise ValueError("Access denied.")
        # allow editing if pending or active, block closed edits
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            raise ValueError("Cannot edit a closed recommendation.")

        old_entry = rec_orm.entry
        old_notes = getattr(rec_orm, 'notes', None)
        rec_orm.entry = new_entry
        if new_notes is not None:
            rec_orm.notes = new_notes

        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="ENTRY_NOTES_UPDATED", event_data={"old_entry": float(old_entry), "new_entry": float(new_entry), "old_notes": old_notes, "new_notes": new_notes}))
        self.notify_reply(rec_id, f"✏️ Entry/Notes for #{rec_orm.asset} updated.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def manage_profit_stop_async(self, rec_id: int, user_id: str, profit_stop_price: Optional[Decimal], mode: str = "SET", db_session: Session = None) -> RecommendationEntity:
        """
        Manage profit stop configuration for a recommendation.
        Non-invasive: stores configuration as RecommendationEvent and sets attribute if model supports it.
        mode: "SET" or "CLEAR"
        """
        # Accept either external db_session or operate via get_for_update caller context
        external_session = db_session is not None
        if not external_session:
            # create a new scoped session for the operation
            with session_scope() as s:
                return await self.manage_profit_stop_async(rec_id, user_id, profit_stop_price, mode, db_session=s)

        # within provided db_session
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm:
            raise ValueError(f"Recommendation #{rec_id} not found.")
        if rec_orm.analyst_id != user.id:
            raise ValueError("Access denied.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            raise ValueError("Cannot configure profit stop on closed recommendation.")

        # apply
        if mode == "SET" and profit_stop_price is not None:
            # set attribute if exists, else only record event
            if hasattr(rec_orm, 'profit_stop'):
                try:
                    rec_orm.profit_stop = profit_stop_price
                except Exception:
                    # skip setting attribute if incompatible
                    logger.debug("profit_stop attribute present but write failed; will store event only.")
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="PROFIT_STOP_SET", event_data={"profit_stop": float(profit_stop_price)}))
            self.notify_reply(rec_id, f"🔒 Profit stop for #{rec_orm.asset} set at {profit_stop_price:g}.", db_session)
        else:
            # CLEAR
            if hasattr(rec_orm, 'profit_stop'):
                try:
                    rec_orm.profit_stop = None
                except Exception:
                    logger.debug("profit_stop attribute present but clearing failed; event recorded.")
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="PROFIT_STOP_CLEARED"))
            self.notify_reply(rec_id, f"🔓 Profit stop for #{rec_orm.asset} cleared.", db_session)

        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    # -------------------
    # Close / Partial close
    # -------------------
    async def close_recommendation_async(self, rec_id: int, user_id: str, exit_price: Decimal, db_session: Session, reason: str = "MANUAL_CLOSE") -> RecommendationEntity:
        """
        Final close: closes remaining open percent entirely and marks recommendation CLOSED.
        Records FINAL_PARTIAL_CLOSE event containing pnl_on_part for remaining piece.
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
            raise ValueError("Recommendation is already closed.")

        remaining_percent = Decimal(str(rec_orm.open_size_percent))
        if remaining_percent > 0:
            raw_pct = _pct(rec_orm.entry, exit_price, rec_orm.side)
            pnl_on_part = _normalize_pct_value(raw_pct)
            event_data = {
                "price": float(exit_price),
                "closed_percent": float(remaining_percent),
                "remaining_percent": 0.0,
                "pnl_on_part": float(pnl_on_part),
                "triggered_by": reason
            }
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="FINAL_PARTIAL_CLOSE", event_data=event_data))

        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.exit_price = exit_price
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.open_size_percent = Decimal(0)

        self.notify_reply(rec_id, f"✅ Signal #{rec_orm.asset} manually closed at {exit_price:g}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, price: Decimal, db_session: Session, triggered_by: str = "MANUAL") -> RecommendationEntity:
        """
        Perform a neutral 'partial close' (may result in profit or loss).
        - reduces open_size_percent
        - records event PARTIAL_CLOSE_MANUAL or PARTIAL_CLOSE_AUTO
        - notifies with explicit profit/loss wording
        - if position falls below threshold (0.1%) closes fully
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

        event_type = "PARTIAL_CLOSE_AUTO" if triggered_by == "AUTO" else "PARTIAL_CLOSE_MANUAL"
        event_data = {
            "price": float(price),
            "closed_percent": float(actual_close_percent),
            "remaining_percent": float(rec_orm.open_size_percent),
            "pnl_on_part": float(pnl_on_part)
        }
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type=event_type, event_data=event_data))

        if pnl_on_part >= 0:
            notif_text = f"💰 Partial Close (Profit) on #{rec_orm.asset}. Closed {actual_close_percent:g}% at {price:g} ({pnl_on_part:+.2f}%)."
        else:
            notif_text = f"⚠️ Partial Close (Loss Mgt) on #{rec_orm.asset}. Closed {actual_close_percent:g}% at {price:g} ({pnl_on_part:+.2f}%)."
        notif_text += f"\nRemaining: {rec_orm.open_size_percent:g}%"
        self.notify_reply(rec_id, notif_text, db_session)

        if rec_orm.open_size_percent < Decimal('0.1'):
            logger.info("Position #%s fully closed via partial close (remaining < 0.1).", rec_id)
            return await self.close_recommendation_async(rec_id, user_id, price, db_session, reason="PARTIAL_CLOSE_FINAL")
        else:
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)
            return self.repo._to_entity(rec_orm)

    # -------------------
    # Event processors (external triggers)
    # -------------------
    async def process_invalidation_event(self, item_id: int):
        """Called when a pending rec is invalidated (e.g., SL hit before entry)."""
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
        """Activate a pending recommendation (set to ACTIVE)."""
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
        """When SL is hit by market - final close at price."""
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.ACTIVE:
                return
            analyst_user_id = str(rec.analyst.telegram_user_id) if getattr(rec, "analyst", None) else None
            await self.close_recommendation_async(rec.id, analyst_user_id, price, db_session, reason="SL_HIT")

    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        """
        Called when a TP is hit in market data:
         - logs TP{n}_HIT (idempotent)
         - triggers partial close if target specifies close_percent
         - possibly auto-close on final TP if exit_strategy requires it
        """
        with session_scope() as db_session:
            rec_orm = self.repo.get_for_update(db_session, item_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE:
                return

            event_type = f"TP{target_index}_HIT"
            # idempotency: if event already recorded, ignore
            if any(e.event_type == event_type for e in (rec_orm.events or [])):
                logger.debug("TP event already processed for %s %s", item_id, event_type)
                return

            db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={"price": float(price)}))
            self.notify_reply(rec_orm.id, f"🎯 Signal #{rec_orm.asset} hit TP{target_index} at {price}!", db_session=db_session)

            # process partial close if configured
            try:
                target_info = rec_orm.targets[target_index - 1]
            except (IndexError, Exception):
                target_info = {}
            close_percent = Decimal(str(target_info.get("close_percent", 0))) if target_info else Decimal(0)

            if close_percent > 0:
                # call partial close as analyst user (system-trigger)
                analyst_user_id = str(rec_orm.analyst.telegram_user_id) if getattr(rec_orm, "analyst", None) else None
                await self.partial_close_async(rec_orm.id, analyst_user_id, close_percent, price, db_session, triggered_by="AUTO")
                # refresh rec_orm after partial close
                rec_orm = self.repo.get_for_update(db_session, item_id)

            is_final_tp = (target_index == len(rec_orm.targets))
            if (rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final_tp) or rec_orm.open_size_percent < Decimal('0.1'):
                analyst_user_id = str(rec_orm.analyst.telegram_user_id) if getattr(rec_orm, "analyst", None) else None
                await self.close_recommendation_async(rec_orm.id, analyst_user_id, price, db_session, reason="AUTO_CLOSE_FINAL_TP")
            else:
                await self._commit_and_dispatch(db_session, rec_orm)

    # -------------------
    # Helpers / Utilities exposed for UI handlers
    # -------------------
    def _get_or_create_system_user(self, db_session: Session) -> User:
        """Ensure a system user exists (telegram_user_id = -1)."""
        system_user = db_session.query(User).filter(User.telegram_user_id == -1).first()
        if not system_user:
            system_user = User(telegram_user_id=-1, username='system', user_type=UserType.ANALYST.value, is_active=True)
            db_session.add(system_user)
            db_session.flush()
        elif not system_user.is_active:
            system_user.is_active = True
        return system_user

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """Return open recommendations and user's trades combined, used by UI panels."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user:
            return []
        open_positions: List[RecommendationEntity] = []

        if user.user_type == UserType.ANALYST:
            recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in recs_orm:
                if rec_entity := self.repo._to_entity(rec):
                    setattr(rec_entity, 'is_user_trade', False)
                    open_positions.append(rec_entity)

        trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trades_orm:
            trade_entity = RecommendationEntity(
                id=trade.id,
                asset=Symbol(trade.asset),
                side=Side(trade.side),
                entry=Price(trade.entry),
                stop_loss=Price(trade.stop_loss),
                targets=Targets(trade.targets),
                status=RecommendationStatusEntity.ACTIVE,
                order_type=OrderType.MARKET,
                created_at=trade.created_at
            )
            setattr(trade_entity, 'is_user_trade', True)
            open_positions.append(trade_entity)

        open_positions.sort(key=lambda p: getattr(p, "created_at", datetime.min), reverse=True)
        return open_positions

    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """Return detailed RecommendationEntity for UI. Respects ownership (analyst/trader)."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user:
            return None

        if position_type == 'rec':
            # only analyst owner may view rec details in this context
            if user.user_type != UserType.ANALYST:
                return None
            rec_orm = self.repo.get(db_session, position_id)
            if not rec_orm or rec_orm.analyst_id != user.id:
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
        """Return recent assets for quick selection UI; fallback to defaults."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user:
            return []
        if user.user_type == UserType.ANALYST:
            recs = self.repo.get_open_recs_for_analyst(db_session, user.id)
            assets = list(dict.fromkeys([r.asset for r in recs]))[:limit]
        else:
            trades = self.repo.get_open_trades_for_trader(db_session, user.id)
            assets = list(dict.fromkeys([t.asset for t in trades]))[:limit]

        if len(assets) < limit:
            default_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
            for a in default_assets:
                if a not in assets and len(assets) < limit:
                    assets.append(a)
        return assets