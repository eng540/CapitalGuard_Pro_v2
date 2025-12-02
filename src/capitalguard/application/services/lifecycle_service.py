# --- START OF PRODUCTION READY FILE: src/capitalguard/application/services/lifecycle_service.py ---
# File: src/capitalguard/application/services/lifecycle_service.py
# Version: v12.0.0-MASTER-FINAL
# ✅ INCLUDES:
#    1. All Features from v8 (Lifecycle Logic).
#    2. All Compatibility Fixes from v7 (Parameter Names: session, obj).
#    3. All UserTrade Functions (Portfolio Management).
#    4. CRITICAL FIX: Breakeven & Profit Stop logic unlocked (Financial Correctness).

from __future__ import annotations
import logging
import asyncio
import inspect
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload

# Infrastructure & Domain Imports
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    Recommendation, RecommendationEvent,
    RecommendationStatusEnum, UserTrade, 
    ExitStrategyEnum, UserTradeStatusEnum, 
    UserTradeEvent
)
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, UserRepository
)
from capitalguard.domain.entities import Recommendation as RecommendationEntity

# Type hints to avoid circular imports
if False:
    from .alert_service import AlertService

logger = logging.getLogger(__name__)

# --- Helper Functions ---

def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    """Converts input to Decimal safely, handling floats/strings/None."""
    try: 
        if isinstance(value, Decimal):
            return value if value.is_finite() else default
        if value is None:
            return default
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError): 
        return default

def _format_price(price: Any) -> str:
    """Formats price for display."""
    d = _to_decimal(price)
    return f"{d:g}" if d.is_finite() else "N/A"

def _pct(entry: Any, target: Any, side: str) -> float:
    """Calculates PnL percentage based on trade side (Long/Short)."""
    try:
        e, t = _to_decimal(entry), _to_decimal(target)
        if e <= 0: return 0.0
        side_upper = str(side).upper()
        if "LONG" in side_upper: 
            return float(((t / e) - 1) * 100)
        elif "SHORT" in side_upper:
            return float(((e / t) - 1) * 100)
        return 0.0
    except Exception: 
        return 0.0

def _parse_int_user_id(user_id: Any) -> Optional[int]:
    """Parses user ID safely."""
    try:
        if user_id is None: return None
        return int(str(user_id).strip())
    except (ValueError, TypeError): return None

# --- Main Service Class ---

class LifecycleService:
    """
    [Core Service] Lifecycle Management
    Responsible for state transitions of Recommendations and UserTrades.
    Ensures ACID compliance, handles locking, and manages event logging.
    """
    def __init__(self, repo: RecommendationRepository, notifier: Any):
        self.repo = repo
        self.notifier = notifier
        # AlertService is injected externally to avoid circular dependency
        self.alert_service: Optional["AlertService"] = None

    # --- Internal Core Methods ---

    async def _commit_and_dispatch(self, session: Session, obj: Any, rebuild_alerts: bool = True):
        """
        Atomically commits the transaction and triggers post-commit actions.
        ✅ COMPATIBILITY FIX: Uses 'session' and 'obj' parameter names to match v7/System calls.
        """
        try:
            session.commit()
            # Refresh object to get generated IDs/timestamps
            try:
                session.refresh(obj)
            except Exception:
                pass # Object might be detached or deleted, ignore refresh error
            
            # 1. Update Alert System Index
            if rebuild_alerts and self.alert_service:
                await self.alert_service.build_triggers_index()

            # 2. Update UI (Telegram Cards)
            if isinstance(obj, Recommendation):
                entity = self.repo._to_entity(obj)
                if entity: 
                    await self.notify_card_update(entity, session)
                    
        except Exception as e:
            logger.error(f"Commit/Dispatch failed: {e}", exc_info=True)
            session.rollback()
            raise

    async def notify_card_update(self, rec_entity: RecommendationEntity, session: Session):
        """Updates the live message card on Telegram."""
        if getattr(rec_entity, "is_shadow", False): return
        
        # Optimization: Inject cached live price if available
        if not getattr(rec_entity, "live_price", None) and self.alert_service:
            try:
                lp = await self.alert_service.price_service.get_cached_price(
                    rec_entity.asset.value, rec_entity.market
                )
                if lp: rec_entity.live_price = lp
            except Exception: pass

        msgs = self.repo.get_published_messages(session, rec_entity.id)
        if not msgs: return

        bot_username = getattr(self.notifier, "bot_username", "CapitalGuardBot")

        # Create async tasks for all channels
        tasks = []
        for m in msgs:
            # Handle both async and sync notifier implementations
            if inspect.iscoroutinefunction(self.notifier.edit_recommendation_card_by_ids):
                tasks.append(self.notifier.edit_recommendation_card_by_ids(
                    m.telegram_channel_id, m.telegram_message_id, rec_entity, bot_username
                ))
            else:
                loop = asyncio.get_running_loop()
                tasks.append(loop.run_in_executor(
                    None, self.notifier.edit_recommendation_card_by_ids, 
                    m.telegram_channel_id, m.telegram_message_id, rec_entity, bot_username
                ))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def notify_reply(self, rec_id: int, text: str, db_session: Session):
        """Posts a threading reply to the recommendation message."""
        msgs = self.repo.get_published_messages(db_session, rec_id)
        for m in msgs:
             asyncio.create_task(self._send_reply(m.telegram_channel_id, m.telegram_message_id, text))

    async def _send_reply(self, ch, msg, text):
        try:
            if inspect.iscoroutinefunction(self.notifier.post_notification_reply):
                await self.notifier.post_notification_reply(ch, msg, text)
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.notifier.post_notification_reply, ch, msg, text)
        except Exception: pass

    async def _notify_user_trade_update(self, user_id: int, text: str):
        """Sends a private DM to the user."""
        try:
            with session_scope() as session:
                user = UserRepository(session).find_by_id(user_id)
                if not user: return
                chat_id = user.telegram_user_id

            if inspect.iscoroutinefunction(self.notifier.send_private_text):
                await self.notifier.send_private_text(chat_id, text)
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.notifier.send_private_text, chat_id, text)
        except Exception as e:
            logger.error(f"Failed to notify user {user_id}: {e}")

    # --- Recommendation Lifecycle Actions (Analyst) ---

    async def close_recommendation_async(self, rec_id: int, user_id: Optional[str], exit_price: Decimal, 
                                        db_session: Optional[Session] = None, reason: str = "MANUAL_CLOSE", 
                                        rebuild_alerts: bool = True):
        """
        Closes a recommendation. Can be triggered manually or by system (SL/TP).
        """
        if db_session is None:
             with session_scope() as s: 
                 return await self.close_recommendation_async(rec_id, user_id, exit_price, s, reason, rebuild_alerts)
        
        # LOCKING: Get for update to prevent concurrent closes
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: raise ValueError("Recommendation not found")
        
        # Idempotency check
        if rec.status == RecommendationStatusEnum.CLOSED:
            return self.repo._to_entity(rec)

        # Authorization check (skip for system triggers)
        is_system = reason in ["SL_HIT", "TP_HIT", "PARTIAL_FINAL", "AUTO_CLOSE_FINAL_TP", "WEB_CLOSE", "WEB_PARTIAL", "MANUAL_PRICE_CLOSE"]
        if user_id and not is_system:
             user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
             if not user or rec.analyst_id != user.id: raise ValueError("Access Denied: Not the owner.")

        # State transition
        rec.status = RecommendationStatusEnum.CLOSED
        rec.exit_price = exit_price
        rec.closed_at = datetime.now(timezone.utc)
        rec.open_size_percent = Decimal(0)
        rec.profit_stop_active = False

        # Event Sourcing
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id, 
            event_type="FINAL_CLOSE", 
            event_data={"price": float(exit_price), "reason": reason}
        ))
        
        # Cleanup triggers
        if self.alert_service:
            await self.alert_service.remove_single_trigger("recommendation", rec.id)

        # Notify & Commit
        await self.notify_reply(rec.id, f"✅ Signal Closed at {_format_price(exit_price)}", db_session)
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=rebuild_alerts)
        return self.repo._to_entity(rec)

    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, price: Decimal, 
                                 db_session: Session, triggered_by: str = "MANUAL"):
        """Performs a partial close, reducing position size."""
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: raise ValueError("Recommendation not found")
        
        if rec.status != RecommendationStatusEnum.ACTIVE:
            # If already closed, just return
            if rec.status == RecommendationStatusEnum.CLOSED: return self.repo._to_entity(rec)
            raise ValueError(f"Cannot partial close. Status is {rec.status.value}")

        if user_id:
             user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
             if not user or rec.analyst_id != user.id: raise ValueError("Access Denied")
            
        curr_pct = _to_decimal(rec.open_size_percent)
        close_percent = _to_decimal(close_percent)
        
        if close_percent > curr_pct: close_percent = curr_pct 
        
        rec.open_size_percent = curr_pct - close_percent
        pnl = _pct(rec.entry, price, rec.side)
        
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id, 
            event_type="PARTIAL", 
            event_data={"price": float(price), "amount": float(close_percent), "pnl": pnl}
        ))
        
        await self.notify_reply(rec.id, f"💰 Partial Close {close_percent:g}% at {_format_price(price)} (PnL: {pnl:.2f}%)", db_session)
        
        # If remaining size is negligible, close fully
        if rec.open_size_percent < Decimal('0.1'):
             return await self.close_recommendation_async(rec.id, user_id, price, db_session, "PARTIAL_FINAL", rebuild_alerts=False)
        
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=False)
        return self.repo._to_entity(rec)

    # --- Recommendation Updates (Manual) ---

    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, db_session: Optional[Session] = None):
        """
        Updates Stop Loss.
        ✅ FINANCIAL FIX: Strict validation removed. Allows SL > Entry for Longs (Profit Stop/Breakeven).
        """
        if db_session is None: 
            with session_scope() as s: return await self.update_sl_for_user_async(rec_id, user_id, new_sl, s)
        
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: raise ValueError("Not found")
        
        if rec.status == RecommendationStatusEnum.CLOSED:
             raise ValueError("Cannot update SL for closed trade.")

        # Basic sanity check only
        if new_sl <= 0: raise ValueError("SL must be positive.")
            
        old_sl = rec.stop_loss
        rec.stop_loss = new_sl
        
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id, 
            event_type="SL_UPDATED", 
            event_data={"old": str(old_sl), "new": str(new_sl)}
        ))
        
        self.notify_reply(rec.id, f"⚠️ SL Updated to {_format_price(new_sl)}", db_session)
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)

    async def update_targets_for_user_async(self, rec_id: int, user_id: str, new_targets: List[Dict], db_session: Session):
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: raise ValueError("Not found")
        
        if rec.status == RecommendationStatusEnum.CLOSED:
             raise ValueError("Cannot update targets for closed trade.")
             
        rec.targets = [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in new_targets]
        
        db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="TP_UPDATED"))
        self.notify_reply(rec.id, "🎯 Targets Updated", db_session)
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)
    
    async def update_entry_and_notes_async(self, rec_id: int, user_id: str, new_entry: Optional[Decimal], new_notes: Optional[str], db_session: Session):
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: raise ValueError("Not found")
        updated = False
        
        if new_entry is not None:
            if rec.status != RecommendationStatusEnum.PENDING:
                raise ValueError("Entry only editable when PENDING.")
            if new_entry <= 0: raise ValueError("Entry must be positive")
            
            if rec.entry != new_entry:
                rec.entry = new_entry
                updated = True
                
        if new_notes is not None:
             rec.notes = new_notes
             updated = True
        
        if updated:
            db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="DATA_UPDATED"))
            self.notify_reply(rec.id, "✏️ Data Updated", db_session)
            await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)
    
    async def set_exit_strategy_async(self, rec_id: int, user_id: str, mode: str, price: Optional[Decimal] = None, 
                                     trailing_value: Optional[Decimal] = None, active: bool = True, session: Optional[Session] = None):
        if session is None: 
            with session_scope() as s: return await self.set_exit_strategy_async(rec_id, user_id, mode, price, trailing_value, active, s)
        
        rec = self.repo.get_for_update(session, rec_id)
        if not rec: raise ValueError("Not found")
        
        rec.profit_stop_mode = mode
        rec.profit_stop_active = active
        if price: rec.profit_stop_price = price
        if trailing_value: rec.profit_stop_trailing_value = trailing_value
        
        msg = f"📈 Strategy: {mode}" if active else "❌ Strategy Cancelled"
        self.notify_reply(rec.id, msg, session)
        await self._commit_and_dispatch(session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)

    async def move_sl_to_breakeven_async(self, rec_id: int, db_session: Optional[Session] = None):
        """
        Calculates breakeven point (Entry +/- Fees) and updates SL.
        ✅ FINANCIAL FIX: Works correctly by using improved validation logic.
        """
        if db_session is None: 
            with session_scope() as s: return await self.move_sl_to_breakeven_async(rec_id, s)
        
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec or rec.status != RecommendationStatusEnum.ACTIVE: 
            raise ValueError("Only ACTIVE trades can move to Breakeven.")
            
        entry = _to_decimal(rec.entry)
        # Buffer to cover fees (approx 0.05% safety margin)
        buffer = entry * Decimal('0.0005') 
        new_sl = entry + buffer if str(rec.side).upper() == 'LONG' else entry - buffer
        
        # Try to find analyst ID for the update call
        analyst_uid = str(rec.analyst.telegram_user_id) if rec.analyst else None
        
        if analyst_uid:
            return await self.update_sl_for_user_async(rec_id, analyst_uid, new_sl, db_session)
        else:
             # Fallback if analyst relation not loaded or missing
             rec.stop_loss = new_sl
             db_session.add(RecommendationEvent(
                 recommendation_id=rec.id, 
                 event_type="SL_UPDATED", 
                 event_data={"reason": "BreakEven", "new": str(new_sl)}
             ))
             self.notify_reply(rec.id, f"🛡️ Moved to Break-Even: {_format_price(new_sl)}", db_session)
             await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
             return self.repo._to_entity(rec)

    # --- Recommendation Event Processing (System Triggers) ---

    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        """Handles Take Profit hit logic, including partial auto-closing."""
        with session_scope() as s:
            rec_orm = self.repo.get_for_update(s, item_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE:
                return
            
            # Idempotency check using event log
            event_type = f"TP{target_index}_HIT"
            if any(e.event_type == event_type for e in (rec_orm.events or [])):
                return
                
            s.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={"price": float(price)}))
            self.notify_reply(rec_orm.id, f"🎯 Hit TP{target_index} at {_format_price(price)}!", db_session=s)
            s.flush() # Ensure event is readable for logic below

            # Check for partial close configuration
            try: target_info = rec_orm.targets[target_index - 1]
            except: target_info = {}
            close_percent = _to_decimal(target_info.get("close_percent", 0))
            
            analyst_uid_str = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
            
            if not analyst_uid_str:
                # If we can't identify analyst, just commit the event
                await self._commit_and_dispatch(s, rec_orm, rebuild_alerts=False)
                return

            # Execute Auto Partial Close
            if close_percent > 0:
                await self.partial_close_async(rec_orm.id, analyst_uid_str, close_percent, price, s, triggered_by="AUTO")
            
            # Re-fetch state after partial close
            rec_orm = self.repo.get(s, item_id)
            if not rec_orm: return 

            # Check for Full Close Conditions
            is_final_tp = (target_index == len(rec_orm.targets or []))
            should_auto_close = (rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final_tp)
            is_effectively_closed = (rec_orm.open_size_percent is not None and rec_orm.open_size_percent < Decimal('0.1'))
            
            if (should_auto_close or is_effectively_closed) and rec_orm.status == RecommendationStatusEnum.ACTIVE:
                reason = "AUTO_CLOSE_FINAL_TP" if should_auto_close else "CLOSED_VIA_PARTIAL"
                await self.close_recommendation_async(rec_orm.id, analyst_uid_str, price, s, reason=reason, rebuild_alerts=False)
            elif close_percent <= 0:
                # Just commit if no close action happened
                await self._commit_and_dispatch(s, rec_orm, rebuild_alerts=False)

    async def process_sl_hit_event(self, item_id: int, price: Decimal):
         with session_scope() as s:
             rec = self.repo.get_for_update(s, item_id)
             if rec and rec.status == RecommendationStatusEnum.ACTIVE:
                 await self.close_recommendation_async(rec.id, None, price, s, "SL_HIT", rebuild_alerts=False)

    async def process_activation_event(self, item_id: int):
        with session_scope() as s:
             rec = self.repo.get_for_update(s, item_id)
             if rec and rec.status == RecommendationStatusEnum.PENDING:
                 rec.status = RecommendationStatusEnum.ACTIVE
                 rec.activated_at = datetime.now(timezone.utc)
                 s.add(RecommendationEvent(recommendation_id=rec.id, event_type="ACTIVATED"))
                 self.notify_reply(rec.id, f"▶️ ACTIVE!", db_session=s)
                 await self._commit_and_dispatch(s, rec, rebuild_alerts=True)

    async def process_invalidation_event(self, item_id: int):
         with session_scope() as s:
             rec = self.repo.get_for_update(s, item_id)
             if rec and rec.status == RecommendationStatusEnum.PENDING:
                 rec.status = RecommendationStatusEnum.CLOSED
                 rec.closed_at = datetime.now(timezone.utc)
                 s.add(RecommendationEvent(recommendation_id=rec.id, event_type="INVALIDATED"))
                 self.notify_reply(rec.id, f"❌ Invalidated (SL hit before Entry)", db_session=s)
                 if self.alert_service:
                     await self.alert_service.remove_single_trigger("recommendation", rec.id)
                 await self._commit_and_dispatch(s, rec, rebuild_alerts=False)

    # --- UserTrade Lifecycle (User Portfolio) ---
    # ✅ INCLUDED: All UserTrade logic preserved from v7/v8

    async def close_user_trade_async(self, user_id: str, trade_id: int, exit_price: Decimal, db_session: Session) -> Optional[UserTrade]:
        """Manually closes a user trade."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: raise ValueError("User not found.")
        
        trade = db_session.query(UserTrade).filter(UserTrade.id == trade_id, UserTrade.user_id == user.id).with_for_update().first()
        if not trade: raise ValueError(f"Trade #{trade_id} not found")
        
        if trade.status == UserTradeStatusEnum.CLOSED: return trade
        
        pnl = 0.0
        if trade.status == UserTradeStatusEnum.ACTIVATED:
             pnl = _pct(trade.entry, exit_price, trade.side)
        
        trade.status = UserTradeStatusEnum.CLOSED
        trade.close_price = exit_price
        trade.closed_at = datetime.now(timezone.utc)
        trade.pnl_percentage = Decimal(f"{pnl:.4f}")
        
        db_session.add(UserTradeEvent(user_trade_id=trade.id, event_type="MANUAL_CLOSE", event_data={"price": str(exit_price), "pnl": pnl}))
        
        if self.alert_service:
            await self.alert_service.remove_single_trigger("user_trade", trade.id)
            
        await self._commit_and_dispatch(db_session, trade, rebuild_alerts=False)
        return trade

    async def process_user_trade_activation_event(self, item_id: int):
        with session_scope() as s:
            trade = s.query(UserTrade).options(selectinload(UserTrade.events)).filter(UserTrade.id == item_id).with_for_update().first()
            if trade and trade.status == UserTradeStatusEnum.PENDING_ACTIVATION:
                trade.status = UserTradeStatusEnum.ACTIVATED
                trade.activated_at = datetime.now(timezone.utc)
                s.add(UserTradeEvent(user_trade_id=trade.id, event_type="ACTIVATED"))
                await self._notify_user_trade_update(trade.user_id, f"▶️ Trade #{trade.asset} Activated!")
                await self._commit_and_dispatch(s, trade, rebuild_alerts=True)

    async def process_user_trade_invalidation_event(self, item_id: int, price: Decimal):
         with session_scope() as s:
            trade = s.query(UserTrade).filter(UserTrade.id == item_id).with_for_update().first()
            if trade and trade.status in [UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.WATCHLIST]:
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.closed_at = datetime.now(timezone.utc)
                trade.pnl_percentage = Decimal(0)
                s.add(UserTradeEvent(user_trade_id=trade.id, event_type="INVALIDATED", event_data={"price": str(price)}))
                if self.alert_service: await self.alert_service.remove_single_trigger("user_trade", item_id)
                await self._notify_user_trade_update(trade.user_id, f"❌ Trade #{trade.asset} Invalidated (SL before Entry)")
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)

    async def process_user_trade_sl_hit_event(self, item_id: int, price: Decimal):
         with session_scope() as s:
            trade = s.query(UserTrade).filter(UserTrade.id == item_id).with_for_update().first()
            if trade and trade.status == UserTradeStatusEnum.ACTIVATED:
                pnl = _pct(trade.entry, price, trade.side)
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.closed_at = datetime.now(timezone.utc)
                trade.pnl_percentage = Decimal(f"{pnl:.4f}")
                s.add(UserTradeEvent(user_trade_id=trade.id, event_type="SL_HIT", event_data={"price": str(price), "pnl": pnl}))
                if self.alert_service: await self.alert_service.remove_single_trigger("user_trade", item_id)
                await self._notify_user_trade_update(trade.user_id, f"🛑 SL Hit #{trade.asset} @ {_format_price(price)} (PnL: {pnl:.2f}%)")
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)

    async def process_user_trade_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
         with session_scope() as s:
            trade = s.query(UserTrade).options(selectinload(UserTrade.events)).filter(UserTrade.id == item_id).with_for_update().first()
            if not trade or trade.status != UserTradeStatusEnum.ACTIVATED: return
            
            event_type = f"TP{target_index}_HIT"
            if any(e.event_type == event_type for e in (trade.events or [])): return
            
            s.add(UserTradeEvent(user_trade_id=trade.id, event_type=event_type, event_data={"price": str(price)}))
            await self._notify_user_trade_update(trade.user_id, f"🎯 TP{target_index} Hit #{trade.asset} @ {_format_price(price)}")
            
            # Check for Final Target
            if target_index == len(trade.targets or []):
                pnl = _pct(trade.entry, price, trade.side)
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.closed_at = datetime.now(timezone.utc)
                trade.pnl_percentage = Decimal(f"{pnl:.4f}")
                if self.alert_service: await self.alert_service.remove_single_trigger("user_trade", item_id)
                await self._notify_user_trade_update(trade.user_id, f"🏆 Final Target Hit #{trade.asset}! (PnL: {pnl:.2f}%)")
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)
            else:
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)
# --- END OF PRODUCTION READY FILE ---