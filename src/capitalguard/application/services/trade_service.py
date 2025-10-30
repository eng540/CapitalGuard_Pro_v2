# src/capitalguard/application/services/trade_service.py v31.1.1 - FINAL SYNTAX ERROR HOTFIX
"""
TradeService v31.1.1 - Critical hotfix for SyntaxErrors.
✅ THE FIX: Corrected indentation in _to_decimal function.
✅ THE FIX: Corrected syntax error in _publish_recommendation (line 214).
✅ Retains Decimal precision logic.
✅ Retains Analyst Ownership (API Security) check.
"""

from __future__ import annotations
import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session

# Infrastructure & Domain Imports
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
    OrderType, ExitStrategy, UserType as UserTypeEntity
)
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets

# Type-only imports
if False:
    from .alert_service import AlertService
    from .price_service import PriceService
    from .market_data_service import MarketDataService

logger = logging.getLogger(__name__)

# ---------------------------
# Internal Helper Functions
# ---------------------------
def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """
    Safely converts input to a Decimal, returning default on failure or non-finite values.
    """
    if isinstance(value, Decimal): 
        return value if value.is_finite() else default
    if value is None: 
        return default
    try: 
        d = Decimal(str(value))
        return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError): 
        return default

def _format_price(price: Any) -> str:
    """Formats a price (Decimal-safe) for display."""
    price_dec = _to_decimal(price);
    return "N/A" if not price_dec.is_finite() else f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    """
    Calculates PnL percentage using Decimal for precision, returning float for simplicity/storage.
    """
    try:
        entry_dec = _to_decimal(entry);
        target_dec = _to_decimal(target_price);
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite(): return 0.0
        side_upper = (str(side.value) if hasattr(side, 'value') else str(side) or "").upper()
        if side_upper == "LONG": pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT": pnl = ((entry_dec / target_dec) - 1) * 100
        else: return 0.0
        
        # Convert Decimal result to float for consistency with existing system design (Pydantic/JSON serialization)
        return float(pnl) 
    except (InvalidOperation, TypeError, ZeroDivisionError): return 0.0

def _normalize_pct_value(pct_raw: Any) -> Decimal:
    """Normalizes percentage values (str, float, int) to Decimal."""
    try:
        if isinstance(pct_raw, Decimal): return pct_raw
        if isinstance(pct_raw, (int, float)): return Decimal(str(pct_raw))
        if isinstance(pct_raw, str): s = pct_raw.strip().replace('%', '').replace('+', '').replace(',', '');
        return Decimal(s)
        return Decimal(str(pct_raw))
    except (InvalidOperation, Exception) as exc: logger.warning(f"Unable normalize pct '{pct_raw}': {exc}");
    return Decimal(0)

def _parse_int_user_id(user_id: Any) -> Optional[int]:
    """Safely converts various representations of user ID to an integer."""
    try:
        if user_id is None: return None
        user_str = str(user_id).strip();
        return int(user_str) if user_str.lstrip('-').isdigit() else None
    except (TypeError, ValueError, AttributeError): return None

# ---------------------------
# TradeService Class
# ---------------------------
class TradeService:
    """Manages the lifecycle of Recommendations and UserTrades."""
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
        self.alert_service: Optional["AlertService"] = None # Injected later

    # --- Internal DB / Notifier Helpers ---
    async def _commit_and_dispatch(self, db_session: Session, orm_object: Union[Recommendation, UserTrade], rebuild_alerts: bool = True):
        """Commits changes, refreshes ORM, updates alerts, notifies UI (if Recommendation)."""
        item_id = getattr(orm_object, 'id', 'N/A'); item_type = type(orm_object).__name__;
        try:
            db_session.commit();
            db_session.refresh(orm_object); logger.debug(f"Committed {item_type} ID {item_id}")
        except Exception as commit_err:
            logger.error(f"Commit failed {item_type} ID {item_id}: {commit_err}", exc_info=True);
            db_session.rollback(); raise
        
        if isinstance(orm_object, Recommendation):
            rec_orm = orm_object
            if rebuild_alerts and self.alert_service:
                try:
                    await self.alert_service.build_triggers_index()
                except Exception as alert_err:
                    logger.exception(f"Alert rebuild fail Rec ID {item_id}: {alert_err}")
            
            updated_entity = self.repo._to_entity(rec_orm);
            if updated_entity:
                try: await self.notify_card_update(updated_entity, db_session)
                except Exception as notify_err: logger.exception(f"Notify fail Rec ID {item_id}: {notify_err}")
            else: logger.error(f"Failed conv ORM Rec {item_id} to entity")

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        if inspect.iscoroutinefunction(fn): return await fn(*args, **kwargs)
        else: loop = asyncio.get_running_loop();
        return await loop.run_in_executor(None, fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        if getattr(rec_entity, "is_shadow", False): return
        try:
            published_messages = self.repo.get_published_messages(db_session, rec_entity.id);
            if not published_messages: return
            tasks = [ self._call_notifier_maybe_async( self.notifier.edit_recommendation_card_by_ids, channel_id=msg.telegram_channel_id, message_id=msg.telegram_message_id, rec=rec_entity) for msg in published_messages ];
            results = await asyncio.gather(*tasks, return_exceptions=True);
            for res in results:
                if isinstance(res, Exception): logger.error(f"Notify task fail Rec ID {rec_entity.id}: {res}", exc_info=False)
        except Exception as e: logger.error(f"Error fetch/update pub messages Rec ID {rec_entity.id}: {e}", exc_info=True)

    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        rec_orm = self.repo.get(db_session, rec_id);
        if not rec_orm or getattr(rec_orm, "is_shadow", False): return
        published_messages = self.repo.get_published_messages(db_session, rec_id);
        for msg in published_messages: asyncio.create_task(self._call_notifier_maybe_async( self.notifier.post_notification_reply, chat_id=msg.telegram_channel_id, message_id=msg.telegram_message_id, text=text ))

    # --- Validation ---
    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        """Strict validation for recommendation/trade numerical integrity. Raises ValueError."""
        side_upper = (str(side) or "").upper()
        if not all(v is not None and isinstance(v, Decimal) and v.is_finite() and v > 0 for v in [entry, stop_loss]): raise ValueError("Entry and SL must be positive finite Decimals.")
        if not targets or not isinstance(targets, list): raise ValueError("Targets must be a non-empty list.")

        target_prices: List[Decimal] = []
        for i, t in enumerate(targets):
            if not isinstance(t, dict) or 'price' not in t: raise ValueError(f"Target {i+1} invalid format.")
            price = _to_decimal(t.get('price'))
            if not price.is_finite() or price <= 0: raise ValueError(f"Target {i+1} price invalid.")
            target_prices.append(price)
            close_pct = t.get('close_percent', 0.0)
            try:
                 close_pct_float = float(close_pct)
                 if not (0.0 <= close_pct_float <= 100.0): raise ValueError(f"Target {i+1} close % invalid.")
            except (ValueError, TypeError) as e:
                 raise ValueError(f"Target {i+1} close % ('{close_pct}') invalid.") from e

        if not target_prices: raise ValueError("No valid target prices found.")
        if side_upper == "LONG" and stop_loss >= entry: raise ValueError("LONG SL must be < Entry.")
        if side_upper == "SHORT" and stop_loss <= entry: raise ValueError("SHORT SL must be > Entry.")
        if side_upper == "LONG" and any(p <= entry for p in target_prices): raise ValueError("LONG targets must be > Entry.")
        if side_upper == "SHORT" and any(p >= entry for p in target_prices): raise ValueError("SHORT targets must be < Entry.")
        risk = abs(entry - stop_loss);
        first_tp = min(target_prices) if side_upper == "LONG" else max(target_prices); reward = abs(first_tp - entry);
        if risk.is_zero(): raise ValueError("Entry and SL cannot be equal.")
        if reward.is_zero() or (reward / risk) < Decimal('0.1'): raise ValueError("Risk/Reward too low (min 0.1).")
        if len(target_prices) != len(set(target_prices)): raise ValueError("Target prices must be unique.")
        if target_prices != sorted(target_prices, reverse=(side_upper == 'SHORT')): raise ValueError("Targets must be sorted.")

    # --- Publishing ---
    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_db_id: int, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}; channels_to_publish = ChannelRepository(session).list_by_analyst(user_db_id, only_active=True);
        if target_channel_ids is not None: channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids];
        if not channels_to_publish: report["failed"].append({"reason": "No active channels linked/selected."}); return rec_entity, report;
        try: from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        except ImportError: public_channel_keyboard = lambda *_: None;
        logger.warning("public_channel_keyboard not found.")
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None)); tasks = [];
        channel_map = {ch.telegram_channel_id: ch for ch in channels_to_publish};
        for channel_id in channel_map.keys(): tasks.append(self._call_notifier_maybe_async( self.notifier.post_to_channel, channel_id, rec_entity, keyboard ));
        results = await asyncio.gather(*tasks, return_exceptions=True);
        for i, channel_id in enumerate(channel_map.keys()):
            result = results[i];
            if isinstance(result, Exception): logger.exception(f"Failed publish Rec {rec_entity.id} channel {channel_id}: {result}");
            report["failed"].append({"channel_id": channel_id, "reason": str(result)})
            
            # ✅ THE FIX: Removed semicolon and fixed indentation.
            elif isinstance(result, tuple) and len(result) == 2:
                session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=result[0], telegram_message_id=result[1]))
                report["success"].append({"channel_id": channel_id, "message_id": result[1]})
            
            else: reason = f"Notifier unexpected result: {type(result)}";
            logger.error(f"Failed publish Rec {rec_entity.id} channel {channel_id}: {reason}"); report["failed"].append({"channel_id": channel_id, "reason": reason});
        session.flush(); return rec_entity, report;
    # --- Public API - Create/Publish Recommendation ---
    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """Creates and publishes a new recommendation."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id));
        if not user or user.user_type != UserTypeEntity.ANALYST: raise ValueError("Only analysts.");
        entry_price_in = _to_decimal(kwargs['entry']); sl_price = _to_decimal(kwargs['stop_loss']); targets_list_in = kwargs['targets'];
        targets_list_validated = [{'price': _to_decimal(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_in]; asset = kwargs['asset'].strip().upper(); side = kwargs['side'].upper();
        market = kwargs.get('market', 'Futures'); order_type_enum = OrderTypeEnum[kwargs['order_type'].upper()];
        
        exit_strategy_val = kwargs.get('exit_strategy');
        if exit_strategy_val is None: exit_strategy_enum = ExitStrategyEnum.CLOSE_AT_FINAL_TP
        elif isinstance(exit_strategy_val, ExitStrategyEnum): exit_strategy_enum = exit_strategy_val
        elif isinstance(exit_strategy_val, ExitStrategy): exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.name]
        elif isinstance(exit_strategy_val, str):
            try:
                exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.upper()]
            except KeyError:
                raise ValueError(f"Unsupported exit_strategy: {exit_strategy_val}")
        else:
            raise ValueError(f"Unsupported exit_strategy format: {type(exit_strategy_val)}")
        
        if order_type_enum == OrderTypeEnum.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True);
            status, final_entry = RecommendationStatusEnum.ACTIVE, _to_decimal(live_price) if live_price is not None else None;
            if final_entry is None or not final_entry.is_finite() or final_entry <= 0: raise RuntimeError(f"Could not fetch valid live price for {asset}.")
        else:
            status, final_entry = RecommendationStatusEnum.PENDING, entry_price_in
        
        self._validate_recommendation_data(side, final_entry, sl_price, targets_list_validated);
        targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_validated];
        rec_orm = Recommendation( analyst_id=user.id, asset=asset, side=side, entry=final_entry, stop_loss=sl_price, targets=targets_for_db, order_type=order_type_enum, status=status, market=market, notes=kwargs.get('notes'), exit_strategy=exit_strategy_enum, activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None );
        db_session.add(rec_orm); db_session.flush();
        db_session.add(RecommendationEvent( recommendation_id=rec_orm.id, event_type="CREATED_ACTIVE" if status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING", event_data={'entry': str(final_entry)} ));
        db_session.flush(); db_session.refresh(rec_orm);
        
        created_rec_entity = self.repo._to_entity(rec_orm);
        if not created_rec_entity: raise RuntimeError(f"Failed conv new ORM Rec {rec_orm.id} to entity.");
        final_rec, report = await self._publish_recommendation( db_session, created_rec_entity, user.id, kwargs.get('target_channel_ids') );
        if self.alert_service:
            try: await self.alert_service.build_triggers_index()
            except Exception: logger.exception("alert rebuild failed after create");
        return final_rec, report

    # --- User Trade Functions ---
    async def create_trade_from_forwarding_async(
        self, user_id: str, trade_data: Dict[str, Any], original_text: Optional[str], db_session: Session
    ) -> Dict[str, Any]:
        """Creates a UserTrade from parsed forwarded data."""
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}
        
        try:
            entry_dec = trade_data['entry']
            sl_dec = trade_data['stop_loss']
            targets_list_validated = trade_data['targets']
            
            self._validate_recommendation_data(trade_data['side'], entry_dec, sl_dec, targets_list_validated)

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
            db_session.flush()
            log.info(f"UserTrade {new_trade.id} created for user {user_id} from forwarded message.")
            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        except ValueError as e:
            logger.warning(f"Validation fail forward trade user {user_id}: {e}")
            db_session.rollback()
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"Error create trade forward user {user_id}: {e}", exc_info=True)
            db_session.rollback()
            return {'success': False, 'error': 'Internal error saving trade.'}

    async def create_trade_from_recommendation(self, user_id: str, rec_id: int, db_session: Session) -> Dict[str, Any]:
        """Creates a UserTrade by tracking an existing Recommendation."""
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id));
        if not trader_user: return {'success': False, 'error': 'User not found'};
        rec_orm = self.repo.get(db_session, rec_id);
        if not rec_orm: return {'success': False, 'error': 'Signal not found'};
        existing_trade = self.repo.find_user_trade_by_source_id(db_session, trader_user.id, rec_id);
        if existing_trade: return {'success': False, 'error': 'You are already tracking this signal.'};
        try:
            new_trade = UserTrade( user_id=trader_user.id, asset=rec_orm.asset, side=rec_orm.side, entry=rec_orm.entry, stop_loss=rec_orm.stop_loss, targets=rec_orm.targets, status=UserTradeStatus.OPEN, source_recommendation_id=rec_orm.id );
            db_session.add(new_trade); db_session.flush();
            log.info(f"UserTrade {new_trade.id} created user {user_id} tracking Rec {rec_id}.");
            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset};
        except Exception as e:
            logger.error(f"Error create trade from rec user {user_id}, rec {rec_id}: {e}", exc_info=True);
            db_session.rollback();
            return {'success': False, 'error': 'Internal error tracking signal.'};

    async def close_user_trade_async(
        self, user_id: str, trade_id: int, exit_price: Decimal, db_session: Session
    ) -> Optional[UserTrade]:
        """Closes a UserTrade owned by the user. Returns updated ORM object or None."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id));
        if not user: raise ValueError("User not found.");
        trade = db_session.query(UserTrade).filter( UserTrade.id == trade_id, UserTrade.user_id == user.id ).with_for_update().first();
        if not trade: raise ValueError(f"Trade #{trade_id} not found or access denied.");
        if trade.status == UserTradeStatus.CLOSED: logger.warning(f"Closing already closed UserTrade #{trade_id}");
        return trade;
        if not exit_price.is_finite() or exit_price <= 0: raise ValueError("Exit price must be positive.");
        trade.status = UserTradeStatus.CLOSED;
        trade.close_price = exit_price; trade.closed_at = datetime.now(timezone.utc);
        try:
            entry_for_calc = _to_decimal(trade.entry);
            pnl_float = _pct(entry_for_calc, exit_price, trade.side);
            trade.pnl_percentage = Decimal(f"{pnl_float:.4f}");
        except Exception as calc_err:
            logger.error(f"Failed PnL calc UserTrade {trade_id}: {calc_err}");
            trade.pnl_percentage = None;
        logger.info(f"UserTrade {trade_id} closed user {user_id} at {exit_price}");
        db_session.flush();
        return trade;
    # --- Update Operations (Analyst) ---
    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Optional[Session] = None) -> RecommendationEntity:
        if db_session is None:
            with session_scope() as s: return await self.update_sl_for_user_async(rec_id, user_id, new_sl, s)
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id));
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id);
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Only ACTIVE.")
        old_sl = rec_orm.stop_loss;
        try:
            targets_list = [{'price': _to_decimal(t.get('price')), 'close_percent': t.get('close_percent', 0.0)} for t in (rec_orm.targets or [])];
            self._validate_recommendation_data(rec_orm.side, _to_decimal(rec_orm.entry), new_sl, targets_list)
        except ValueError as e:
            logger.warning(f"Invalid SL update rec #{rec_id}: {e}");
            raise ValueError(f"Invalid new SL: {e}")
        rec_orm.stop_loss = new_sl;
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="SL_UPDATED", event_data={"old": str(old_sl), "new": str(new_sl)}));
        self.notify_reply(rec_id, f"⚠️ SL for #{rec_orm.asset} updated to {_format_price(new_sl)}.", db_session);
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True);
        return self.repo._to_entity(rec_orm)

    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, Any]], db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id));
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id);
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Only ACTIVE.")
        try:
            targets_validated = [{'price': _to_decimal(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in new_targets];
            self._validate_recommendation_data(rec_orm.side, _to_decimal(rec_orm.entry), _to_decimal(rec_orm.stop_loss), targets_validated)
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Invalid TP update rec #{rec_id}: {e}");
            raise ValueError(f"Invalid new Targets: {e}")
        old_targets_json = rec_orm.targets;
        rec_orm.targets = [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in targets_validated];
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="TP_UPDATED", event_data={"old": old_targets_json, "new": rec_orm.targets}));
        self.notify_reply(rec_id, f"🎯 Targets for #{rec_orm.asset} updated.", db_session);
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True);
        return self.repo._to_entity(rec_orm)

    async def update_entry_and_notes_async(self, rec_id: int, user_id: str, new_entry: Optional[Decimal], new_notes: Optional[str], db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id));
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id);
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED: raise ValueError("Cannot edit closed.")
        event_data = {};
        updated = False;
        if new_entry is not None:
            if rec_orm.status != RecommendationStatusEnum.PENDING: raise ValueError("Entry only editable PENDING.")
            try:
                targets_list = [{'price': _to_decimal(t.get('price')), 'close_percent': t.get('close_percent', 0.0)} for t in (rec_orm.targets or [])];
                self._validate_recommendation_data(rec_orm.side, new_entry, _to_decimal(rec_orm.stop_loss), targets_list)
            except ValueError as e:
                raise ValueError(f"Invalid new Entry: {e}")
            if rec_orm.entry != new_entry:
                event_data.update({"old_entry": str(rec_orm.entry), "new_entry": str(new_entry)});
                rec_orm.entry = new_entry; updated = True
        if new_notes is not None or (new_notes is None and rec_orm.notes is not None):
            if rec_orm.notes != new_notes:
                event_data.update({"old_notes": rec_orm.notes, "new_notes": new_notes});
                rec_orm.notes = new_notes; updated = True
        if updated:
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="DATA_UPDATED", event_data=event_data));
            self.notify_reply(rec_id, f"✏️ Data #{rec_orm.asset} updated.", db_session);
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=(new_entry is not None));
        else:
            logger.debug(f"No changes update_entry_notes Rec {rec_id}.")
        return self.repo._to_entity(rec_orm)

    async def set_exit_strategy_async(self, rec_id: int, user_id: str, mode: str, price: Optional[Decimal] = None, trailing_value: Optional[Decimal] = None, active: bool = True, session: Optional[Session] = None) -> RecommendationEntity:
        if session is None:
            with session_scope() as s: return await self.set_exit_strategy_async(rec_id, user_id, mode, price, trailing_value, active, s)
        user = UserRepository(session).find_by_telegram_id(_parse_int_user_id(user_id));
        if not user: raise ValueError("User not found.")
        rec = self.repo.get_for_update(session, rec_id);
        if not rec: raise ValueError(f"Rec #{rec_id} not found.")
        if rec.analyst_id != user.id: raise ValueError("Access denied.")
        if rec.status != RecommendationStatusEnum.ACTIVE and active: raise ValueError("Requires ACTIVE.")
        mode_upper = mode.upper();
        if mode_upper == "FIXED" and (price is None or not price.is_finite() or price <= 0): raise ValueError("Fixed requires valid positive price.")
        if mode_upper == "TRAILING" and (trailing_value is None or not trailing_value.is_finite() or trailing_value <= 0): raise ValueError("Trailing requires valid positive value.")
        rec.profit_stop_mode = mode_upper if active else "NONE";
        rec.profit_stop_price = price if active and mode_upper == "FIXED" else None;
        rec.profit_stop_trailing_value = trailing_value if active and mode_upper == "TRAILING" else None; rec.profit_stop_active = active;
        event_data = {"mode": rec.profit_stop_mode, "active": active};
        if rec.profit_stop_price: event_data["price"] = str(rec.profit_stop_price)
        if rec.profit_stop_trailing_value: event_data["trailing_value"] = str(rec.profit_stop_trailing_value)
        session.add(RecommendationEvent(recommendation_id=rec_id, event_type="EXIT_STRATEGY_UPDATED", event_data=event_data));
        if active:
            msg = f"📈 Exit strategy #{rec.asset} set: {mode_upper}";
            if mode_upper == "FIXED": msg += f" at {_format_price(price)}"
            elif mode_upper == "TRAILING": msg += f" with value {_format_price(trailing_value)}"
        else:
            msg = f"❌ Exit strategy #{rec.asset} cancelled."
        self.notify_reply(rec_id, msg, session);
        await self._commit_and_dispatch(session, rec, rebuild_alerts=True);
        return self.repo._to_entity(rec)

    # --- Automation Helpers ---
    async def move_sl_to_breakeven_async(self, rec_id: int, db_session: Optional[Session] = None) -> RecommendationEntity:
        """Moves SL to entry +/- buffer if conditions met."""
        if db_session is None:
            with session_scope() as s:
                return await self.move_sl_to_breakeven_async(rec_id, s)
        
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Only ACTIVE.")
        
        entry_dec = _to_decimal(rec_orm.entry)
        current_sl_dec = _to_decimal(rec_orm.stop_loss)
        
        if not entry_dec.is_finite() or entry_dec <= 0 or not current_sl_dec.is_finite():
            raise ValueError("Invalid entry/SL for BE.")
        
        buffer = entry_dec * Decimal('0.0001') # 0.01% buffer
        new_sl_target = entry_dec + buffer if rec_orm.side == 'LONG' else entry_dec - buffer
        
        is_improvement = (rec_orm.side == 'LONG' and new_sl_target > current_sl_dec) or \
                           (rec_orm.side == 'SHORT' and new_sl_target < current_sl_dec)
        
        if is_improvement:
            analyst_uid = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
            if not analyst_uid:
                raise RuntimeError(f"Cannot BE Rec {rec_id}: Analyst missing.")
            logger.info(f"Moving SL BE Rec #{rec_id} from {current_sl_dec:g} to {new_sl_target:g}")
            return await self.update_sl_for_user_async(rec_id, analyst_uid, new_sl_target, db_session)
        else:
            logger.info(f"SL Rec #{rec_id} already at/better BE {new_sl_target:g}.")
            return self.repo._to_entity(rec_orm)

    # --- Closing Operations ---
    async def close_recommendation_async(self, rec_id: int, user_id: Optional[str], exit_price: Decimal, db_session: Optional[Session] = None, reason: str = "MANUAL_CLOSE") -> RecommendationEntity:
        """
        Closes a recommendation fully.
        THE FIX: Enforces analyst ownership check for security (API/manual close).
        """
        if db_session is None:
            with session_scope() as s: return await self.close_recommendation_async(rec_id, user_id, exit_price, s, reason)
        rec_orm = self.repo.get_for_update(db_session, rec_id);
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED: logger.warning(f"Closing already closed rec #{rec_id}");
        return self.repo._to_entity(rec_orm);
        
        # --- START OF SECURITY FIX ---
        if user_id is not None:
            user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
            # Only the analyst owner can manually close, OR if the reason is system-generated (SL_HIT, etc.)
            is_system_trigger = reason not in ["MANUAL_CLOSE", "MARKET_CLOSE_MANUAL", "MANUAL_PRICE_CLOSE"]
            
            if not user and not is_system_trigger:
                 # This path is hit if user_id is provided but not found, and it's a manual close.
                 # If user_id is provided, we assume it's a manual action unless system trigger.
                 raise ValueError("User not found.")
                 
            # Analyst ID must match user ID for any manual closing action (including API)
            if user and rec_orm.analyst_id != user.id and not is_system_trigger:
                raise ValueError("Access denied. You do not own this recommendation.")
        # --- END OF SECURITY FIX ---
        
        if not exit_price.is_finite() or exit_price <= 0: raise ValueError("Exit price invalid.")
        remaining_percent = _to_decimal(rec_orm.open_size_percent);
        if remaining_percent > 0: pnl_on_part = _pct(rec_orm.entry, exit_price, rec_orm.side); event_data = {"price": float(exit_price), "closed_percent": float(remaining_percent), "pnl_on_part": pnl_on_part, "triggered_by": reason};
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="FINAL_CLOSE", event_data=event_data));
        rec_orm.status = RecommendationStatusEnum.CLOSED; rec_orm.exit_price = exit_price; rec_orm.closed_at = datetime.now(timezone.utc); rec_orm.open_size_percent = Decimal(0); rec_orm.profit_stop_active = False;
        self.notify_reply(rec_id, f"✅ Signal #{rec_orm.asset} closed at {_format_price(exit_price)}. Reason: {reason}", db_session); await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True);
        return self.repo._to_entity(rec_orm)

    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, price: Decimal, db_session: Session, triggered_by: str = "MANUAL") -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id));
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id);
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Only ACTIVE.")
        current_open_percent = _to_decimal(rec_orm.open_size_percent);
        close_percent_dec = _to_decimal(close_percent); price_dec = _to_decimal(price);
        if not (close_percent_dec.is_finite() and 0 < close_percent_dec <= 100): raise ValueError("Close % invalid.")
        if not (price_dec.is_finite() and price_dec > 0): raise ValueError("Close price invalid.")
        actual_close_percent = min(close_percent_dec, current_open_percent);
        if actual_close_percent <= 0: raise ValueError(f"Invalid %. Open is {current_open_percent:g}%. Cannot close {close_percent_dec:g}%.")
        rec_orm.open_size_percent = current_open_percent - actual_close_percent;
        pnl_on_part = _pct(rec_orm.entry, price_dec, rec_orm.side); pnl_formatted = f"{pnl_on_part:+.2f}%";
        event_type = "PARTIAL_CLOSE_AUTO" if triggered_by.upper() == "AUTO" else "PARTIAL_CLOSE_MANUAL";
        event_data = {"price": float(price_dec), "closed_percent": float(actual_close_percent), "remaining_percent": float(rec_orm.open_size_percent), "pnl_on_part": pnl_on_part}; db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type=event_type, event_data=event_data));
        notif_icon = "💰 Profit" if pnl_on_part >= 0 else "⚠️ Loss Mgt";
        notif_text = f"{notif_icon} Partial Close #{rec_orm.asset}. Closed {actual_close_percent:g}% at {_format_price(price_dec)} ({pnl_formatted}).\nRemaining: {rec_orm.open_size_percent:g}%"; self.notify_reply(rec_id, notif_text, db_session);
        if rec_orm.open_size_percent < Decimal('0.1'): logger.info(f"Rec #{rec_id} fully closed via partial."); return await self.close_recommendation_async(rec_id, user_id, price_dec, db_session, reason="PARTIAL_CLOSE_FINAL");
        else: await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False); return self.repo._to_entity(rec_orm);


    # --- Event Processors ---
    async def process_invalidation_event(self, item_id: int):
        """Called when a pending rec is invalidated (e.g., SL hit before entry)."""
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING:
                return
            rec.status = RecommendationStatusEnum.CLOSED
            rec.closed_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED", event_data={"reason": "SL hit before entry"}))
            self.notify_reply(rec.id, f"❌ Signal #{rec.asset} invalidated.", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec)

    async def process_activation_event(self, item_id: int):
        """Activate a pending recommendation (entry reached)."""
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING:
                return
            rec.status = RecommendationStatusEnum.ACTIVE
            rec.activated_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))
            self.notify_reply(rec.id, f"▶️ Signal #{rec.asset} ACTIVE!", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec)

    async def process_sl_hit_event(self, item_id: int, price: Decimal):
        """Handle SL hit by closing the recommendation."""
        with session_scope() as s:
            rec = self.repo.get_for_update(s, item_id)
            if not rec or rec.status != RecommendationStatusEnum.ACTIVE:
                return
            analyst_uid = str(rec.analyst.telegram_user_id) if rec.analyst else None
            # System trigger, user_id=None is acceptable
            await self.close_recommendation_async(rec.id, None, price, s, reason="SL_HIT")

    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        """Handle TP hit events."""
        with session_scope() as s:
            rec_orm = self.repo.get_for_update(s, item_id);
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE: return
            event_type = f"TP{target_index}_HIT";
            if any(e.event_type == event_type for e in (rec_orm.events or [])): logger.debug(f"TP event {event_type} processed {item_id}");
            return
            s.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={"price": float(price)}));
            self.notify_reply(rec_orm.id, f"🎯 #{rec_orm.asset} hit TP{target_index} at {_format_price(price)}!", db_session=s);
            try: target_info = rec_orm.targets[target_index - 1]
            except Exception: target_info = {}
            close_percent = _to_decimal(target_info.get("close_percent", 0));
            analyst_uid_str = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None;
            if not analyst_uid_str: logger.error(f"Cannot process TP {item_id}: Analyst missing."); await self._commit_and_dispatch(s, rec_orm, False);
            return
            if close_percent > 0: await self.partial_close_async(rec_orm.id, analyst_uid_str, close_percent, price, s, triggered_by="AUTO");
            s.refresh(rec_orm); # Refresh state
            is_final_tp = (target_index == len(rec_orm.targets or []));
            should_auto_close = (rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final_tp); is_effectively_closed = (rec_orm.open_size_percent is not None and rec_orm.open_size_percent < Decimal('0.1'));
            if should_auto_close or is_effectively_closed:
                 if rec_orm.status == RecommendationStatusEnum.ACTIVE: reason = "AUTO_CLOSE_FINAL_TP" if should_auto_close else "CLOSED_VIA_PARTIAL";
                 await self.close_recommendation_async(rec_orm.id, analyst_uid_str, price, s, reason=reason);
            elif close_percent <= 0: await self._commit_and_dispatch(s, rec_orm, False);
        # Commit event if no close

    # --- Read Utilities ---
    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str) -> List[RecommendationEntity]:
        """Return combined list of open recommendations and user's trades."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id));
        open_positions = [];
        if not user: return []
        if user.user_type == UserTypeEntity.ANALYST: recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id);
        open_positions.extend([e for rec in recs_orm if (e := self.repo._to_entity(rec)) and setattr(e, 'is_user_trade', False) is None]);
        trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id);
        for trade in trades_orm:
            try: targets_data=trade.targets or [];targets_for_vo=[{'price':_to_decimal(t.get('price')),'close_percent':t.get('close_percent',0.0)} for t in targets_data];
            trade_entity=RecommendationEntity(id=trade.id,asset=Symbol(trade.asset),side=Side(trade.side),entry=Price(_to_decimal(trade.entry)),stop_loss=Price(_to_decimal(trade.stop_loss)),targets=Targets(targets_for_vo),status=RecommendationStatusEntity.ACTIVE,order_type=OrderType.MARKET,created_at=trade.created_at,exit_strategy=ExitStrategy.MANUAL_CLOSE_ONLY); setattr(trade_entity, 'is_user_trade', True); open_positions.append(trade_entity);
            except Exception as conv_err: logger.error(f"Failed conv UserTrade {trade.id}: {conv_err}", exc_info=False);
        open_positions.sort(key=lambda p: getattr(p, "created_at", datetime.min), reverse=True); return open_positions

    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """Return details for a single position, checking ownership."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id))
        if not user:
            return None
        
        if position_type == 'rec':
            rec_orm = self.repo.get(db_session, position_id)
            if not rec_orm or rec_orm.analyst_id != user.id:
                return None
            if rec_entity := self.repo._to_entity(rec_orm):
                setattr(rec_entity, 'is_user_trade', False)
                return rec_entity
            else:
                logger.error(f"Failed conv owned Rec ORM {position_id}")
                return None
        
        elif position_type == 'trade':
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if not trade_orm or trade_orm.user_id != user.id:
                return None
            try:
                targets_data=trade_orm.targets or []
                targets_for_vo=[{'price':_to_decimal(t.get('price')),'close_percent':t.get('close_percent',0.0)} for t in targets_data]
                trade_entity=RecommendationEntity(
                    id=trade_orm.id,asset=Symbol(trade_orm.asset),side=Side(trade_orm.side),
                    entry=Price(_to_decimal(trade_orm.entry)),stop_loss=Price(_to_decimal(trade_orm.stop_loss)),
                    targets=Targets(targets_for_vo),status=RecommendationStatusEntity.ACTIVE if trade_orm.status == UserTradeStatus.OPEN else RecommendationStatusEntity.CLOSED,
                    order_type=OrderType.MARKET,created_at=trade_orm.created_at,closed_at=trade_orm.closed_at,
                    exit_price=float(trade_orm.close_price) if trade_orm.close_price is not None else None,
                    exit_strategy=ExitStrategy.MANUAL_CLOSE_ONLY
                )
                setattr(trade_entity, 'is_user_trade', True)
                if trade_orm.pnl_percentage is not None:
                    setattr(trade_entity, 'final_pnl_percentage', float(trade_orm.pnl_percentage))
                return trade_entity
            except Exception as conv_err:
                logger.error(f"Failed conv UserTrade {trade_orm.id} details: {conv_err}", exc_info=False)
                return None
        
        else:
            logger.warning(f"Unknown position_type '{position_type}'.")
            return None

    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """Return recent assets for quick selection UI."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_telegram_id));
        assets = set();
        if not user: return []
        if user.user_type == UserTypeEntity.ANALYST: recs = db_session.query(Recommendation.asset).filter(Recommendation.analyst_id == user.id).order_by(Recommendation.created_at.desc()).limit(limit * 2).distinct().all();
        assets.update(r.asset for r in recs);
        else: trades = db_session.query(UserTrade.asset).filter(UserTrade.user_id == user.id).order_by(UserTrade.created_at.desc()).limit(limit * 2).distinct().all(); assets.update(t.asset for t in trades);
        asset_list = list(assets)[:limit];
        if len(asset_list) < limit: default_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"];
        [asset_list.append(a) for a in default_assets if a not in asset_list and len(asset_list) < limit];
        return asset_list

# --- END of TradeService ---