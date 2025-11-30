# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/lifecycle_service.py ---
# File: src/capitalguard/application/services/lifecycle_service.py
# Version: v5.1.1-COMPLETE (Full Feature Set with Hotfix)
# ✅ THE FIX: Renamed 'rebuild' arg to 'rebuild_alerts' in _commit_and_dispatch to match callers.
# ✅ PRESERVED: All original functionality including UserTrade operations and detailed validation.

from __future__ import annotations
import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Infrastructure & Domain Imports
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    PublishedMessage, Recommendation, RecommendationEvent, User,
    RecommendationStatusEnum, UserTrade, 
    OrderTypeEnum, ExitStrategyEnum,
    UserTradeStatusEnum, 
    UserTradeEvent
)
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, ChannelRepository, UserRepository
)
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType as OrderTypeEntity,
    ExitStrategy as ExitStrategyEntity,
    UserType as UserTypeEntity
)
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets

# Type-only imports
if False:
    from .alert_service import AlertService

logger = logging.getLogger(__name__)

# --- Helper Functions ---
def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
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
    price_dec = _to_decimal(price)
    return "N/A" if not price_dec.is_finite() else f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    try:
        entry_dec = _to_decimal(entry)
        target_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite():
            return 0.0
        side_upper = (str(side.value) if hasattr(side, 'value') else str(side) or "").upper()
        if side_upper == "LONG":
            pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT":
            pnl = ((entry_dec / target_dec) - 1) * 100
        else:
            return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError):
        return 0.0

def _parse_int_user_id(user_id: Any) -> Optional[int]:
    try:
        if user_id is None:
            return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.lstrip('-').isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None

def _validate_recommendation_data(side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
    side_upper = (str(side) or "").upper()
    if not all(v is not None and isinstance(v, Decimal) and v.is_finite() and v > 0 for v in [entry, stop_loss]):
        raise ValueError("Entry and SL must be positive finite Decimals.")
    if not targets or not isinstance(targets, list):
        raise ValueError("Targets must be a non-empty list.")
    target_prices: List[Decimal] = []
    for i, t in enumerate(targets):
        if not isinstance(t, dict) or 'price' not in t:
            raise ValueError(f"Target {i+1} invalid format.")
        price = _to_decimal(t.get('price'))
        if not price.is_finite() or price <= 0:
            raise ValueError(f"Target {i+1} price invalid.")
        target_prices.append(price)

    if not target_prices: raise ValueError("No valid target prices found.")
    if side_upper == "LONG" and stop_loss >= entry: raise ValueError("LONG SL must be < Entry.")
    if side_upper == "SHORT" and stop_loss <= entry: raise ValueError("SHORT SL must be > Entry.")
    if side_upper == "LONG" and any(p <= entry for p in target_prices): raise ValueError("LONG targets must be > Entry.")
    if side_upper == "SHORT" and any(p >= entry for p in target_prices): raise ValueError("SHORT targets must be < Entry.")
    logger.debug("Data validation successful (Lifecycle check).")

# --- End Helpers ---

class LifecycleService:
    """
    [R2 Service]
    مسؤولة حصريًا عن "إدارة دورة حياة" الكيانات (التفعيل، الإغلاق، التحديثات، الأحداث).
    """
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
    ):
        self.repo = repo
        self.notifier = notifier
        # يتم حقن هذه الخدمة لاحقًا (Circular Dependency)
        self.alert_service: Optional["AlertService"] = None

    # --- Internal DB / Notifier Helpers ---
    async def _commit_and_dispatch(self, db_session: Session, orm_object: Union[Recommendation, UserTrade], rebuild_alerts: bool = True):
        """
        ✅ THE FIX: Renamed parameter from 'rebuild' to 'rebuild_alerts' to match all callers
        """
        item_id = getattr(orm_object, 'id', 'N/A')
        item_type = type(orm_object).__name__
        try:
            db_session.commit()
            db_session.refresh(orm_object)
            logger.debug(f"Committed {item_type} ID {item_id}")
        except Exception as commit_err:
            logger.error(f"Commit failed {item_type} ID {item_id}: {commit_err}", exc_info=True)
            db_session.rollback()
            raise

        if isinstance(orm_object, Recommendation):
            rec_orm = orm_object
            if rebuild_alerts and self.alert_service:
                try:
                    logger.info(f"Rebuilding full alert index on request for Rec ID {item_id}...")
                    await self.alert_service.build_triggers_index()
                except Exception as alert_err:
                    logger.exception(f"Alert rebuild fail Rec ID {item_id}: {alert_err}")

            updated_entity = self.repo._to_entity(rec_orm)
            if updated_entity:
                try:
                    await self.notify_card_update(updated_entity, db_session)
                except Exception as notify_err:
                    logger.exception(f"Notify fail Rec ID {item_id}: {notify_err}")
            else:
                logger.error(f"Failed conv ORM Rec {item_id} to entity")
        
        elif isinstance(orm_object, UserTrade):
             if rebuild_alerts and self.alert_service:
                try:
                    logger.info(f"Rebuilding full alert index on request for UserTrade ID {item_id}...")
                    await self.alert_service.build_triggers_index()
                except Exception as alert_err:
                    logger.exception(f"Alert rebuild fail UserTrade ID {item_id}: {alert_err}")

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        else:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity, db_session: Session):
        """تحديث بطاقة التوصية في جميع القنوات المنشورة فيها."""
        if getattr(rec_entity, "is_shadow", False): return
        
        # ✅ FEATURE: حقن السعر الحي فقط عند التحديث (Update Phase)
        if not getattr(rec_entity, "live_price", None) and self.alert_service:
            try:
                lp = await self.alert_service.price_service.get_cached_price(rec_entity.asset.value, rec_entity.market)
                if lp: rec_entity.live_price = lp
            except: pass

        try:
            published_messages = self.repo.get_published_messages(db_session, rec_entity.id)
            if not published_messages: return
            
            # ✅ FEATURE: Dynamic bot username
            bot_username = getattr(self.notifier, "bot_username", "CapitalGuardBot")
            
            tasks = [ self._call_notifier_maybe_async(
                self.notifier.edit_recommendation_card_by_ids,
                channel_id=msg.telegram_channel_id,
                message_id=msg.telegram_message_id,
                rec=rec_entity,
                bot_username=bot_username
            ) for msg in published_messages ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error(f"Notify task fail Rec ID {rec_entity.id}: {res}", exc_info=False)
        except Exception as e:
            logger.error(f"Error fetch/update pub messages Rec ID {rec_entity.id}: {e}", exc_info=True)

    async def _notify_user_trade_update(self, user_id: int, text: str):
        """Sends a private notification to a user about their trade."""
        try:
            with session_scope() as session:
                user = UserRepository(session).find_by_id(user_id)
                if not user:
                    logger.warning(f"Cannot notify UserTrade update, user DB ID {user_id} not found.")
                    return
                telegram_user_id = user.telegram_user_id
            
            await self._call_notifier_maybe_async(
                self.notifier.send_private_text, 
                chat_id=telegram_user_id, 
                text=text
            )
        except Exception as e:
            logger.error(f"Failed to send private notification to user {user_id}: {e}", exc_info=True)
    
    def notify_reply(self, rec_id: int, text: str, db_session: Session):
        """Posts a reply to all published messages for a recommendation."""
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

    # --- Public API - Close Trade (Trader) ---
    async def close_user_trade_async(
        self, user_id: str, trade_id: int, exit_price: Decimal, db_session: Session
    ) -> Optional[UserTrade]:
        """
        [Core Algorithm]
        إغلاق صفقة متداول شخصية وحساب PnL.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        
        trade = db_session.query(UserTrade).filter( UserTrade.id == trade_id, UserTrade.user_id == user.id ).with_for_update().first()
        if not trade: raise ValueError(f"Trade #{trade_id} not found or access denied.")
        
        if trade.status == UserTradeStatusEnum.CLOSED:
            logger.warning(f"Closing already closed UserTrade #{trade_id}")
            return trade
        
        if trade.status not in [UserTradeStatusEnum.ACTIVATED, UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.WATCHLIST]:
            raise ValueError(f"Can only close trades that are active or pending. Status is {trade.status.value}.")
        
        if not exit_price.is_finite() or exit_price <= 0:
            raise ValueError("Exit price must be positive.")

        # [Core Algorithm] حساب PnL فقط إذا كانت الصفقة "مفعلة"
        if trade.status == UserTradeStatusEnum.ACTIVATED:
            try:
                entry_for_calc = _to_decimal(trade.entry)
                pnl_float = _pct(entry_for_calc, exit_price, trade.side)
            except Exception as calc_err:
                logger.error(f"Failed PnL calc UserTrade {trade_id}: {calc_err}")
                pnl_float = 0.0
        else:
            # الصفقات التي أُغلقت من WATCHLIST أو PENDING_ACTIVATION لم "تُفعل"،
            # لذا PnL = 0
            pnl_float = 0.0
            trade.activated_at = None 

        trade.status = UserTradeStatusEnum.CLOSED
        trade.close_price = exit_price
        trade.closed_at = datetime.now(timezone.utc)
        trade.pnl_percentage = Decimal(f"{pnl_float:.4f}")
        logger.info(f"UserTrade {trade_id} closed user {user_id} at {exit_price} (PnL: {pnl_float:.2f}%)")
        
        # [ADR-001] إزالة الفهرس الذكي
        if self.alert_service:
            await self.alert_service.remove_single_trigger(item_type="user_trade", item_id=trade_id)
            
        db_session.flush()
        return trade

    # --- Public API - Close Recommendation (Analyst) ---
    async def close_recommendation_async(self, rec_id: int, user_id: Optional[str], exit_price: Decimal, db_session: Optional[Session] = None, reason: str = "MANUAL_CLOSE", rebuild_alerts: bool = True) -> RecommendationEntity:
        """
        [Core Algorithm]
        إغلاق توصية محلل رسمية.
        """
        if db_session is None:
            with session_scope() as s:
                return await self.close_recommendation_async(rec_id, user_id, exit_price, s, reason, rebuild_alerts)
        
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            logger.warning(f"Closing already closed rec #{rec_id}")
            return self.repo._to_entity(rec_orm)
        
        if user_id is not None:
            user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
            is_system_trigger = reason not in ["MANUAL_CLOSE", "MARKET_CLOSE_MANUAL", "MANUAL_PRICE_CLOSE"]
            if not user and not is_system_trigger:
                raise ValueError("User not found.")
            if user and rec_orm.analyst_id != user.id and not is_system_trigger:
                raise ValueError("Access denied. You do not own this recommendation.")
        
        if not exit_price.is_finite() or exit_price <= 0:
            raise ValueError("Exit price invalid.")
        
        remaining_percent = _to_decimal(rec_orm.open_size_percent)
        if remaining_percent > 0:
            pnl_on_part = _pct(rec_orm.entry, exit_price, rec_orm.side)
            event_data = {"price": float(exit_price), "closed_percent": float(remaining_percent), "pnl_on_part": pnl_on_part, "triggered_by": reason}
        else:
            event_data = {"price": float(exit_price), "closed_percent": 0, "pnl_on_part": 0.0, "triggered_by": reason}
        
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="FINAL_CLOSE", event_data=event_data))
        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.exit_price = exit_price
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.open_size_percent = Decimal(0)
        rec_orm.profit_stop_active = False
        
        # [ADR-001] إزالة الفهرس الذكي
        if self.alert_service:
            await self.alert_service.remove_single_trigger(item_type="recommendation", item_id=rec_id)
            
        self.notify_reply(rec_id, f"✅ Signal #{rec_orm.asset} closed at {_format_price(exit_price)}. Reason: {reason}", db_session)
        
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=rebuild_alerts)
        return self.repo._to_entity(rec_orm)

    # --- Public API - Update Operations (Analyst) ---
    
    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, price: Decimal, db_session: Session, triggered_by: str = "MANUAL") -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Only ACTIVE.")
        
        current_open_percent = _to_decimal(rec_orm.open_size_percent)
        close_percent_dec = _to_decimal(close_percent)
        price_dec = _to_decimal(price)
        
        if not (close_percent_dec.is_finite() and 0 < close_percent_dec <= 100):
            raise ValueError("Close % invalid.")
        if not (price_dec.is_finite() and price_dec > 0):
            raise ValueError("Close price invalid.")
        
        actual_close_percent = min(close_percent_dec, current_open_percent)
        if actual_close_percent <= 0:
            raise ValueError(f"Invalid %. Open is {current_open_percent:g}%. Cannot close {close_percent_dec:g}%.")

        rec_orm.open_size_percent = current_open_percent - actual_close_percent
        pnl_on_part = _pct(rec_orm.entry, price_dec, rec_orm.side)
        pnl_formatted = f"{pnl_on_part:+.2f}%"
        
        event_type = "PARTIAL_CLOSE_AUTO" if triggered_by.upper() == "AUTO" else "PARTIAL_CLOSE_MANUAL"
        event_data = {"price": float(price_dec), "closed_percent": float(actual_close_percent), "remaining_percent": float(rec_orm.open_size_percent), "pnl_on_part": pnl_on_part}
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type=event_type, event_data=event_data))
        
        notif_icon = "💰 Profit" if pnl_on_part >= 0 else "⚠️ Loss Mgt"
        notif_text = f"{notif_icon} Partial Close #{rec_orm.asset}. Closed {actual_close_percent:g}% at {_format_price(price_dec)} ({pnl_formatted}).\nRemaining: {rec_orm.open_size_percent:g}%"
        self.notify_reply(rec_id, notif_text, db_session)
        
        if rec_orm.open_size_percent < Decimal('0.1'):
            logger.info(f"Rec #{rec_id} fully closed via partial.")
            return await self.close_recommendation_async(rec_id, user_id, price_dec, db_session, reason="PARTIAL_CLOSE_FINAL", rebuild_alerts=False)
        else:
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=False)
        return self.repo._to_entity(rec_orm)

    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Optional[Session] = None) -> RecommendationEntity:
        if db_session is None:
            with session_scope() as s: return await self.update_sl_for_user_async(rec_id, user_id, new_sl, s)
        
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Only ACTIVE.")
        
        old_sl = rec_orm.stop_loss
        try:
            targets_list = [{'price': _to_decimal(t.get('price')), 'close_percent': t.get('close_percent', 0.0)} for t in (rec_orm.targets or [])]
            _validate_recommendation_data(rec_orm.side, _to_decimal(rec_orm.entry), new_sl, targets_list)
        except ValueError as e:
            logger.warning(f"Invalid SL update rec #{rec_id}: {e}")
            raise ValueError(f"Invalid new SL: {e}")
            
        rec_orm.stop_loss = new_sl
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="SL_UPDATED", event_data={"old": str(old_sl), "new": str(new_sl)}))
        self.notify_reply(rec_id, f"⚠️ SL for #{rec_orm.asset} updated to {_format_price(new_sl)}.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    
    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict[str, Any]], db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status != RecommendationStatusEnum.ACTIVE: raise ValueError("Only ACTIVE.")
        
        try:
            targets_validated = [{'price': _to_decimal(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in new_targets]
            _validate_recommendation_data(rec_orm.side, _to_decimal(rec_orm.entry), _to_decimal(rec_orm.stop_loss), targets_validated)
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Invalid TP update rec #{rec_id}: {e}")
            raise ValueError(f"Invalid new Targets: {e}")
            
        old_targets_json = rec_orm.targets
        rec_orm.targets = [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in targets_validated]
        db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="TP_UPDATED", event_data={"old": old_targets_json, "new": rec_orm.targets}))
        self.notify_reply(rec_id, f"🎯 Targets for #{rec_orm.asset} updated.", db_session)
        await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=True)
        return self.repo._to_entity(rec_orm)

    async def update_entry_and_notes_async(self, rec_id: int, user_id: str, new_entry: Optional[Decimal], new_notes: Optional[str], db_session: Session) -> RecommendationEntity:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm: raise ValueError(f"Rec #{rec_id} not found.")
        if rec_orm.analyst_id != user.id: raise ValueError("Access denied.")
        if rec_orm.status == RecommendationStatusEnum.CLOSED: raise ValueError("Cannot edit closed.")
        
        event_data = {}
        updated = False
        
        if new_entry is not None:
            if rec_orm.status != RecommendationStatusEnum.PENDING:
                raise ValueError("Entry only editable PENDING.")
            try:
                targets_list = [{'price': _to_decimal(t.get('price')), 'close_percent': t.get('close_percent', 0.0)} for t in (rec_orm.targets or [])]
                _validate_recommendation_data(rec_orm.side, new_entry, _to_decimal(rec_orm.stop_loss), targets_list)
            except ValueError as e:
                raise ValueError(f"Invalid new Entry: {e}")
            
            if rec_orm.entry != new_entry:
                event_data.update({"old_entry": str(rec_orm.entry), "new_entry": str(new_entry)})
                rec_orm.entry = new_entry
                updated = True
                
        if new_notes is not None or (new_notes is None and rec_orm.notes is not None):
            if rec_orm.notes != new_notes:
                event_data.update({"old_notes": rec_orm.notes, "new_notes": new_notes})
                rec_orm.notes = new_notes
                updated = True
                
        if updated:
            db_session.add(RecommendationEvent(recommendation_id=rec_id, event_type="DATA_UPDATED", event_data=event_data))
            self.notify_reply(rec_id, f"✏️ Data #{rec_orm.asset} updated.", db_session)
            await self._commit_and_dispatch(db_session, rec_orm, rebuild_alerts=(new_entry is not None))
        else:
            logger.debug(f"No changes update_entry_notes Rec {rec_id}.")
            
        return self.repo._to_entity(rec_orm)

    async def set_exit_strategy_async(self, rec_id: int, user_id: str, mode: str, price: Optional[Decimal] = None, trailing_value: Optional[Decimal] = None, active: bool = True, session: Optional[Session] = None) -> RecommendationEntity:
        if session is None:
            with session_scope() as s: return await self.set_exit_strategy_async(rec_id, user_id, mode, price, trailing_value, active, s)
        
        user = UserRepository(session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        rec = self.repo.get_for_update(session, rec_id)
        if not rec: raise ValueError(f"Rec #{rec_id} not found.")
        if rec.analyst_id != user.id: raise ValueError("Access denied.")
        if rec.status != RecommendationStatusEnum.ACTIVE and active: raise ValueError("Requires ACTIVE.")
        
        mode_upper = mode.upper()
        if mode_upper == "FIXED" and (price is None or not price.is_finite() or price <= 0):
            raise ValueError("Fixed requires valid positive price.")
        if mode_upper == "TRAILING" and (trailing_value is None or not trailing_value.is_finite() or trailing_value <= 0):
            raise ValueError("Trailing requires valid positive value.")
            
        rec.profit_stop_mode = mode_upper if active else "NONE"
        rec.profit_stop_price = price if active and mode_upper == "FIXED" else None
        rec.profit_stop_trailing_value = trailing_value if active and mode_upper == "TRAILING" else None
        rec.profit_stop_active = active
        
        event_data = {"mode": rec.profit_stop_mode, "active": active}
        if rec.profit_stop_price: event_data["price"] = str(rec.profit_stop_price)
        if rec.profit_stop_trailing_value: event_data["trailing_value"] = str(rec.profit_stop_trailing_value)
        session.add(RecommendationEvent(recommendation_id=rec_id, event_type="EXIT_STRATEGY_UPDATED", event_data=event_data))
        
        if active:
            msg = f"📈 Exit strategy #{rec.asset} set: {mode_upper}"
            if mode_upper == "FIXED": msg += f" at {_format_price(price)}"
            elif mode_upper == "TRAILING": msg += f" with value {_format_price(trailing_value)}"
        else:
            msg = f"❌ Exit strategy #{rec.asset} cancelled."
            
        self.notify_reply(rec_id, msg, session)
        await self._commit_and_dispatch(session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)

    # --- Automation Helpers ---
    async def move_sl_to_breakeven_async(self, rec_id: int, db_session: Optional[Session] = None) -> RecommendationEntity:
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

    # --- Event Processors (Recommendation) ---
    
    async def process_invalidation_event(self, item_id: int):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING:
                return
            
            rec.status = RecommendationStatusEnum.CLOSED
            rec.closed_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED", event_data={"reason": "SL hit before entry"}))
            
            if self.alert_service:
                await self.alert_service.remove_single_trigger(item_type="recommendation", item_id=item_id)
                
            self.notify_reply(rec.id, f"❌ Signal #{rec.asset} invalidated.", db_session=db_session)
            await self._commit_and_dispatch(db_session, rec, rebuild_alerts=False)

    async def process_activation_event(self, item_id: int):
        with session_scope() as db_session:
            rec = self.repo.get_for_update(db_session, item_id)
            if not rec or rec.status != RecommendationStatusEnum.PENDING:
                return
                
            rec.status = RecommendationStatusEnum.ACTIVE
            rec.activated_at = datetime.now(timezone.utc)
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))
            self.notify_reply(rec.id, f"▶️ Signal #{rec.asset} ACTIVE!", db_session=db_session)
            
            # [ADR-001] إعادة الفهرسة الذكية
            db_session.commit()
            db_session.refresh(rec, attribute_names=['events', 'analyst'])
            
            if self.alert_service:
                await self.alert_service.remove_single_trigger(item_type="recommendation", item_id=item_id)
                trigger_data = self.alert_service.build_trigger_data_from_orm(rec)
                if trigger_data:
                    await self.alert_service.add_trigger_data(trigger_data)
                
            await self.notify_card_update(self.repo._to_entity(rec), db_session)

    async def process_sl_hit_event(self, item_id: int, price: Decimal):
        with session_scope() as s:
            rec = self.repo.get_for_update(s, item_id)
            if not rec or rec.status != RecommendationStatusEnum.ACTIVE:
                return
            analyst_uid = str(rec.analyst.telegram_user_id) if rec.analyst else None
            await self.close_recommendation_async(rec.id, None, price, s, reason="SL_HIT", rebuild_alerts=False)

    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        with session_scope() as s:
            rec_orm = self.repo.get_for_update(s, item_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE:
                return
            
            event_type = f"TP{target_index}_HIT"
            if any(e.event_type == event_type for e in (rec_orm.events or [])):
                logger.debug(f"TP event {event_type} processed {item_id}")
                return
                
            s.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={"price": float(price)}))
            self.notify_reply(rec_orm.id, f"🎯 #{rec_orm.asset} hit TP{target_index} at {_format_price(price)}!", db_session=s)
            
            try:
                target_info = rec_orm.targets[target_index - 1]
            except Exception:
                target_info = {}
            
            close_percent = _to_decimal(target_info.get("close_percent", 0))
            analyst_uid_str = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
            
            if not analyst_uid_str:
                logger.error(f"Cannot process TP {item_id}: Analyst missing.")
                await self._commit_and_dispatch(s, rec_orm, rebuild_alerts=False)
                return

            if close_percent > 0:
                await self.partial_close_async(rec_orm.id, analyst_uid_str, close_percent, price, s, triggered_by="AUTO")
            
            # ✅ THE FIX: Re-fetch instead of refresh to avoid "not persistent" error
            rec_orm = self.repo.get(s, item_id)
            if not rec_orm: return 

            is_final_tp = (target_index == len(rec_orm.targets or []))
            should_auto_close = (rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final_tp)
            is_effectively_closed = (rec_orm.open_size_percent is not None and rec_orm.open_size_percent < Decimal('0.1'))
            
            if (should_auto_close or is_effectively_closed) and rec_orm.status == RecommendationStatusEnum.ACTIVE:
                reason = "AUTO_CLOSE_FINAL_TP" if should_auto_close else "CLOSED_VIA_PARTIAL"
                await self.close_recommendation_async(rec_orm.id, analyst_uid_str, price, s, reason=reason, rebuild_alerts=False)
            elif close_percent <= 0:
                await self._commit_and_dispatch(s, rec_orm, rebuild_alerts=False)
        
    # --- Event Processors (UserTrade) ---

    async def process_user_trade_activation_event(self, item_id: int):
        """
        [Core Algorithm]
        تفعيل صفقة متداول (WATCHLIST/PENDING -> ACTIVATED).
        """
        with session_scope() as db_session:
            trade = db_session.query(UserTrade).options(
                selectinload(UserTrade.events)
            ).filter(UserTrade.id == item_id).with_for_update().first()
            
            if not trade or trade.status not in [UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION]:
                logger.debug(f"Skipping activation for UserTrade {item_id}, status is {trade.status if trade else 'NOT FOUND'}")
                return

            original_status = trade.status
            trade.status = UserTradeStatusEnum.ACTIVATED
            trade.activated_at = datetime.now(timezone.utc)
            
            db_session.add(UserTradeEvent(
                user_trade_id=trade.id,
                event_type="ACTIVATED",
                event_data={"from_status": original_status.value}
            ))
            
            logger.info(f"UserTrade {item_id} ACTIVATED from status {original_status.value}.")
            
            # [ADR-001] إعادة الفهرسة الذكية
            db_session.commit()
            db_session.refresh(trade, attribute_names=['events', 'user'])

            if self.alert_service:
                await self.alert_service.remove_single_trigger(item_type="user_trade", item_id=item_id)
                trigger_data = self.alert_service.build_trigger_data_from_orm(trade)
                if trigger_data:
                    await self.alert_service.add_trigger_data(trigger_data)

            if original_status == UserTradeStatusEnum.PENDING_ACTIVATION:
                await self._notify_user_trade_update(
                    user_id=trade.user_id,
                    text=f"▶️ Your tracked trade for **#{trade.asset}** is now **ACTIVE** (Entry price reached)!",
                )
    
    async def process_user_trade_invalidation_event(self, item_id: int, price: Decimal):
        """Invalidate a pending UserTrade (SL hit before entry)."""
        with session_scope() as db_session:
            trade = db_session.query(UserTrade).filter(UserTrade.id == item_id).with_for_update().first()
            if not trade or trade.status not in [UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION]:
                return

            original_status = trade.status
            trade.status = UserTradeStatusEnum.CLOSED
            trade.close_price = price
            trade.closed_at = datetime.now(timezone.utc)
            trade.pnl_percentage = Decimal("0.0") 
            
            db_session.add(UserTradeEvent(
                user_trade_id=trade.id,
                event_type="INVALIDATED",
                event_data={"reason": "SL hit before entry", "price": str(price)}
            ))
            
            if self.alert_service:
                await self.alert_service.remove_single_trigger(item_type="user_trade", item_id=item_id)
            
            logger.info(f"UserTrade {item_id} INVALIDATED (closed) from status {original_status.value} at price {price}.")
            await self._commit_and_dispatch(db_session, trade, rebuild_alerts=False)

            if original_status == UserTradeStatusEnum.PENDING_ACTIVATION:
                await self._notify_user_trade_update(
                    user_id=trade.user_id,
                    text=f"❌ Your pending trade for **#{trade.asset}** was **INVALIDATED** (StopLoss hit before entry).",
                )

    async def process_user_trade_sl_hit_event(self, item_id: int, price: Decimal):
        """Handle SL hit for an active UserTrade."""
        with session_scope() as db_session:
            trade = db_session.query(UserTrade).filter(UserTrade.id == item_id).with_for_update().first()
            if not trade or trade.status != UserTradeStatusEnum.ACTIVATED:
                return 

            try:
                pnl_float = _pct(trade.entry, price, trade.side)
            except Exception as e:
                logger.error(f"Failed PnL calc for UserTrade SL hit {item_id}: {e}")
                pnl_float = 0.0
                
            trade.status = UserTradeStatusEnum.CLOSED
            trade.close_price = price
            trade.closed_at = datetime.now(timezone.utc)
            trade.pnl_percentage = Decimal(f"{pnl_float:.4f}")

            db_session.add(UserTradeEvent(
                user_trade_id=trade.id,
                event_type="SL_HIT",
                event_data={"price": str(price), "pnl_percent": pnl_float}
            ))

            if self.alert_service:
                await self.alert_service.remove_single_trigger(item_type="user_trade", item_id=item_id)

            logger.info(f"UserTrade {item_id} CLOSED due to SL_HIT at {price}. PnL: {pnl_float:.2f}%")
            await self._commit_and_dispatch(db_session, trade, rebuild_alerts=False)
            
            await self._notify_user_trade_update(
                user_id=trade.user_id,
                text=f"🛑 **StopLoss Hit** 🛑\nYour trade for **#{trade.asset}** was closed at `{_format_price(price)}`.\nResult: **{pnl_float:+.2f}%**",
            )

    async def process_user_trade_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        """Handle TP hit for an active UserTrade."""
        with session_scope() as db_session:
            trade = db_session.query(UserTrade).options(
                selectinload(UserTrade.events)
            ).filter(UserTrade.id == item_id).with_for_update().first()
            
            if not trade or trade.status != UserTradeStatusEnum.ACTIVATED:
                return

            event_type = f"TP{target_index}_HIT"
            if any(e.event_type == event_type for e in trade.events):
                logger.debug(f"UserTrade TP event {event_type} already processed for {item_id}")
                return
            
            db_session.add(UserTradeEvent(
                user_trade_id=trade.id,
                event_type=event_type,
                event_data={"price": str(price), "target_index": target_index}
            ))

            logger.info(f"UserTrade {item_id} hit TP{target_index} at {price}.")

            is_final_tp = (target_index == len(trade.targets or []))
            
            if is_final_tp:
                try:
                    pnl_float = _pct(trade.entry, price, trade.side)
                except Exception as e:
                    logger.error(f"Failed PnL calc for UserTrade TP hit {item_id}: {e}")
                    pnl_float = 0.0
                
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.closed_at = datetime.now(timezone.utc)
                trade.pnl_percentage = Decimal(f"{pnl_float:.4f}")

                if self.alert_service:
                    await self.alert_service.remove_single_trigger(item_type="user_trade", item_id=item_id)
                    
                logger.info(f"UserTrade {item_id} CLOSED due to FINAL_TP_HIT at {price}. PnL: {pnl_float:.2f}%")
                await self._commit_and_dispatch(db_session, trade, rebuild_alerts=False)
                
                await self._notify_user_trade_update(
                    user_id=trade.user_id,
                    text=f"🏆 **Final Target Hit!** 🏆\nYour trade for **#{trade.asset}** was closed at `{_format_price(price)}`.\nResult: **{pnl_float:+.2f}%**",
                )
            else:
                await self._commit_and_dispatch(db_session, trade, rebuild_alerts=False)
                
                await self._notify_user_trade_update(
                    user_id=trade.user_id,
                    text=f"🎯 **Target Hit!**\nYour trade for **#{trade.asset}** hit **TP{target_index}** at `{_format_price(price)}`.",
                )
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---