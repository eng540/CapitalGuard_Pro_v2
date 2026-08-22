# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/lifecycle_service.py ---
# File: src/capitalguard/application/services/lifecycle_service.py
# Version: v106.0.1-PRODUCTION-MERGED
# ✅ MERGED FIXES:
#    1. ✅ All critical fixes from v106.0.0 maintained
#    2. ✅ Added 'await' to ALL notify_reply calls (from v106)
#    3. ✅ Fixed _commit_and_dispatch to re-raise exceptions after rollback (from v106)
#    4. ✅ Added proper error logging in _send_reply (from v106)
#    5. ✅ Restored validation in update_targets_for_user_async (from v106)
#    6. ✅ Added exit_price validation in close_recommendation_async (from v106)
#    7. ✅ Fixed Decimal comparison and actual_close logic in partial_close_async (from v106)
#    8. ✅ ADDED: Detached Instance Fix with session.refresh() from v200
#    9. ✅ ADDED: Improved user_id parsing for performance from v200

from __future__ import annotations
import hashlib
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
    Recommendation, RecommendationEvent, User,
    RecommendationStatusEnum, UserTrade,
    ExitStrategyEnum,
    UserTradeStatusEnum, 
    UserTradeEvent
)
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
)
from capitalguard.application.services.identity_service import IdentityService

# Type-only imports
if False:
    from .alert_service import AlertService
    from .publication_outbox_service import PublicationOutboxService

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
    # ✅ IMPROVED: Simplified version from v200 for better performance
    try: 
        return int(str(user_id).strip()) if user_id else None
    except (TypeError, ValueError, AttributeError):
        return None

# --- ENHANCED VALIDATION WITH BREAKEVEN SUPPORT ---
def _validate_recommendation_data(side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]], 
                                 is_breakeven_move: bool = False):
    """✅ ENHANCED: Supports breakeven moves with tolerance."""
    side_upper = (str(side) or "").upper()
    
    if not all(v is not None and isinstance(v, Decimal) and v.is_finite() and v > 0 for v in [entry, stop_loss]):
        raise ValueError("Entry and SL must be positive finite Decimals.")
    
    if not targets or not isinstance(targets, list):
        raise ValueError("Targets must be a non-empty list.")
    
    if is_breakeven_move:
        BREAKEVEN_TOLERANCE = Decimal('0.0005')  # 0.05% tolerance
        if side_upper == "LONG":
            max_allowed = entry * (Decimal('1') + BREAKEVEN_TOLERANCE)
            if stop_loss > max_allowed:
                raise ValueError(f"LONG breakeven SL cannot be more than {BREAKEVEN_TOLERANCE*100}% above entry.")
        else:  # SHORT
            min_allowed = entry * (Decimal('1') - BREAKEVEN_TOLERANCE)
            if stop_loss < min_allowed:
                raise ValueError(f"SHORT breakeven SL cannot be more than {BREAKEVEN_TOLERANCE*100}% below entry.")
    else:
        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("LONG SL must be < Entry.")
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("SHORT SL must be > Entry.")
    
    target_prices: List[Decimal] = []
    for i, t in enumerate(targets):
        price = _to_decimal(t.get('price'))
        if not price.is_finite() or price <= 0:
            raise ValueError(f"Target {i+1} price invalid.")
        target_prices.append(price)

    if not target_prices:
        raise ValueError("No valid target prices found.")
    
    if side_upper == "LONG" and any(p <= entry for p in target_prices):
        raise ValueError("LONG targets must be > Entry.")
    if side_upper == "SHORT" and any(p >= entry for p in target_prices):
        raise ValueError("SHORT targets must be < Entry.")
    
    logger.debug("Data validation successful (Lifecycle check).")

# --- Main Service Class ---

class LifecycleService:
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
        outbox_service: Optional["PublicationOutboxService"] = None,
    ):
        self.repo = repo
        self.notifier = notifier
        self.outbox_service = outbox_service
        self.alert_service: Optional["AlertService"] = None

    @staticmethod
    def _event_key(session: Session, recommendation_id: int, fallback: str) -> str:
        event = session.query(RecommendationEvent).filter_by(
            recommendation_id=recommendation_id,
        ).order_by(RecommendationEvent.id.desc()).first()
        return f"event:{event.id}" if event else fallback

    # --- Internal Core Methods ---
    async def _commit_and_dispatch(self, session: Session, obj: Any, rebuild_alerts: bool = True):
        """✅ ENHANCED: Added Detached Instance Fix from v200."""
        try:
            session.commit()
            
            # ✅ ADDED FROM v200: Prevent DetachedInstanceError
            try: 
                session.refresh(obj) 
            except Exception: 
                pass  # Object might be deleted or state invalid, safe to ignore
            
            if rebuild_alerts and self.alert_service:
                self.alert_service.schedule_rebuild_index()

            if isinstance(obj, Recommendation):
                entity = self.repo._to_entity(obj)
                if entity: 
                    await self.notify_card_update(entity, session)
        except Exception as e:
            logger.error(f"Commit dispatch failed: {e}", exc_info=True)
            session.rollback()
            # ✅ CRITICAL FIX: Re-raise to notify caller of failure
            raise

    async def notify_card_update(self, rec_entity: RecommendationEntity, session: Session):
        if getattr(rec_entity, "is_shadow", False): 
            return
        
        if not getattr(rec_entity, "live_price", None) and self.alert_service:
            try:
                lp = await self.alert_service.price_service.get_cached_price(rec_entity.asset.value, rec_entity.market)
                if lp: 
                    rec_entity.live_price = lp
            except Exception:
                pass

        msgs = self.repo.get_published_messages(session, rec_entity.id)
        if not msgs: 
            return

        bot_username = getattr(self.notifier, "bot_username", "CapitalGuardBot")
        
        if self.outbox_service:
            channel_ids = [m.telegram_channel_id for m in msgs]
            event_key = self._event_key(
                session,
                rec_entity.id,
                fallback=f"updated:{rec_entity.id}:{getattr(rec_entity, 'updated_at', None)}",
            )
            self.outbox_service.enqueue_operation(
                session,
                rec_entity.id,
                channel_ids,
                "UPDATE",
                event_key,
            )
            session.commit()
            return

        async def _upd(ch_id, msg_id):
            if inspect.iscoroutinefunction(self.notifier.edit_recommendation_card_by_ids):
                await self.notifier.edit_recommendation_card_by_ids(ch_id, msg_id, rec_entity, bot_username)
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.notifier.edit_recommendation_card_by_ids, ch_id, msg_id, rec_entity, bot_username)

        await asyncio.gather(*[_upd(m.telegram_channel_id, m.telegram_message_id) for m in msgs], return_exceptions=True)

    async def notify_reply(
        self,
        rec_id: int,
        text: str,
        db_session: Session,
        operation: str = "REPLY",
    ):
        """Queue or send a lifecycle reply with durable idempotency."""
        rec = self.repo.get(db_session, rec_id)
        if rec and "Recommendation:" not in text and "🆔" not in text:
            rec_ref = rec.public_ref or f"REC-{rec.id}"
            analyst_code = getattr(getattr(rec, "analyst", None), "analyst_code", None)
            suffix = f"\n🆔 Recommendation: <code>{rec_ref}</code>"
            if analyst_code:
                suffix += f" · {analyst_code}"
            text = f"{text}{suffix}"
        msgs = self.repo.get_published_messages(db_session, rec_id)
        if self.outbox_service:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
            event_key = self._event_key(db_session, rec_id, fallback=f"text:{digest}")
            self.outbox_service.enqueue_operation(
                db_session,
                rec_id,
                [m.telegram_channel_id for m in msgs],
                operation,
                event_key,
                payload={"text": text},
            )
            db_session.commit()
            return
        
        def _handle_task_error(task):
            try:
                task.result()
            except Exception as e:
                logger.error(f"Failed to send reply for rec #{rec_id}: {e}")
        
        for m in msgs:
            task = asyncio.create_task(self._send_reply(m.telegram_channel_id, m.telegram_message_id, text))
            task.add_done_callback(_handle_task_error)

    async def _send_reply(self, ch, msg, text):
        """✅ FIXED: Now logs errors instead of silently ignoring them."""
        try:
            if inspect.iscoroutinefunction(self.notifier.post_notification_reply):
                await self.notifier.post_notification_reply(ch, msg, text)
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.notifier.post_notification_reply, ch, msg, text)
        except Exception as e:
            logger.error(f"Failed to send reply to channel {ch}, message {msg}: {e}")

    async def _notify_user_trade_update(self, user_id: int, text: str, session: Optional[Session] = None):
        try:
            if session is not None:
                user = UserRepository(session).find_by_id(user_id)
                await self._send_private_to_user(user, text)
                return
            with session_scope() as owned_session:
                user = UserRepository(owned_session).find_by_id(user_id)
                await self._send_private_to_user(user, text)
        except Exception as e:
            logger.error(f"Failed to notify user {user_id}: {e}", exc_info=True)

    async def _send_private_to_user(self, user: Optional[User], text: str):
        if not user:
            return
        chat_id = user.telegram_user_id
        if inspect.iscoroutinefunction(self.notifier.send_private_text):
            await self.notifier.send_private_text(chat_id, text)
        else:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.notifier.send_private_text, chat_id, text)

    def _trade_identity_context(self, session: Session, trade: UserTrade) -> Dict[str, Any]:
        trader = getattr(trade, "user", None) or UserRepository(session).find_by_id(trade.user_id)
        trader_code = getattr(trader, "user_code", None) or f"USR-{trade.user_id:06d}"
        display_ref = (
            IdentityService.display_ref(trader_code, "T", trade.trader_sequence)
            if trade.trader_sequence is not None
            else trade.public_ref or f"TRD-{trade.id}"
        )
        source = None
        if trade.source_recommendation_id:
            source = self.repo.get(session, trade.source_recommendation_id)
        source_ref = None
        analyst_code = None
        channel_code = None
        if source:
            source_ref = source.public_ref or f"REC-{source.id}"
            analyst_code = getattr(getattr(source, "analyst", None), "analyst_code", None)
            channel_code = getattr(getattr(getattr(source, "channel", None), "catalog", None), "channel_code", None)
        if not channel_code:
            watched_channel = getattr(trade, "watched_channel", None)
            channel_code = getattr(getattr(watched_channel, "catalog", None), "channel_code", None)
        return {
            "trade_ref": display_ref,
            "trade_public_ref": trade.public_ref or f"TRD-{trade.id}",
            "source_ref": source_ref,
            "analyst_code": analyst_code,
            "channel_code": channel_code,
            "source_type": trade.source_type or ("TRACKED_RECOMMENDATION" if source else "DIRECT_INPUT"),
        }

    async def _notify_trade_event(
        self,
        session: Session,
        trade: UserTrade,
        title: str,
        detail: str,
        mode: str = "AUTO",
    ):
        identity = self._trade_identity_context(session, trade)
        source_line = "📝 Trader Log"
        if identity["source_type"] == "TRACKED_RECOMMENDATION":
            source_line = f"📡 Tracked Signal · {identity['analyst_code'] or 'Analyst'} · {identity['channel_code'] or 'Channel'}"
        text = (
            f"{title}\n"
            f"🆔 UserTrade: <code>{identity['trade_ref']}</code> · {identity['trade_public_ref']}\n"
            f"{source_line}\n"
            + (f"🔗 Recommendation: <code>{identity['source_ref']}</code>\n" if identity["source_ref"] else "")
            + f"⚙️ Source: <b>{mode}</b>\n"
            f"{detail}"
        )
        await self._notify_user_trade_update(trade.user_id, text, session=session)

    async def _sync_tracked_trades_from_recommendation(
        self,
        session: Session,
        recommendation: Recommendation,
        event_type: str,
        event_data: Optional[Dict[str, Any]] = None,
        mode: str = "AUTO",
        title: str = "🔄 Source Recommendation Updated",
    ):
        """Mirror source lifecycle changes into open tracked UserTrade rows."""
        event_data = event_data or {}
        followers = session.query(UserTrade).options(
            selectinload(UserTrade.events),
            selectinload(UserTrade.user),
            selectinload(UserTrade.watched_channel),
        ).filter(
            UserTrade.source_recommendation_id == recommendation.id,
            UserTrade.source_type == "TRACKED_RECOMMENDATION",
            UserTrade.status != UserTradeStatusEnum.CLOSED,
        ).with_for_update().all()
        for trade in followers:
            if event_type == "SOURCE_CLOSED":
                pnl = _pct(trade.entry, event_data.get("price"), trade.side) if trade.activated_at else 0.0
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = _to_decimal(event_data.get("price"))
                trade.pnl_percentage = Decimal(str(pnl))
                trade.open_size_percent = Decimal("0")
                trade.closed_at = datetime.now(timezone.utc)
                notification_title = "🏁 Source Recommendation Closed"
                detail = f"Exit: <code>{_format_price(event_data.get('price'))}</code> · PnL: <b>{pnl:.2f}%</b>"
                if self.alert_service:
                    await self.alert_service.remove_single_trigger("user_trade", trade.id)
            else:
                if "stop_loss" in event_data:
                    trade.stop_loss = event_data["stop_loss"]
                if "targets" in event_data:
                    trade.targets = event_data["targets"]
                if "entry" in event_data and trade.status != UserTradeStatusEnum.ACTIVATED:
                    trade.entry = event_data["entry"]
                if event_type == "SOURCE_PARTIAL":
                    amount = _to_decimal(event_data.get("amount"))
                    trade.open_size_percent = max(Decimal("0"), _to_decimal(trade.open_size_percent) - amount)
                    notification_title = "💰 Source Partial Close"
                    detail = f"Closed: <b>{amount:g}%</b> at <code>{_format_price(event_data.get('price'))}</code>"
                else:
                    notification_title = title
                    detail = f"Event: <b>{event_type}</b>"
            session.add(UserTradeEvent(
                user_trade_id=trade.id,
                event_type=event_type,
                event_data={"source_recommendation_id": recommendation.id, "mode": mode, **event_data},
            ))
            await self._notify_trade_event(session, trade, notification_title, detail, mode=mode)

    # --- Recommendation Lifecycle Actions ---

    async def close_recommendation_async(self, rec_id: int, user_id: Optional[str], exit_price: Decimal, 
                                         db_session: Optional[Session] = None, reason: str = "MANUAL_CLOSE", 
                                         rebuild_alerts: bool = True):
        """✅ FIXED: Added exit_price validation from v106."""
        if db_session is None:
             with session_scope() as s: 
                 return await self.close_recommendation_async(rec_id, user_id, exit_price, s, reason, rebuild_alerts)
        
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: 
            raise ValueError("Rec not found")
        if rec.status == RecommendationStatusEnum.CLOSED: 
            return self.repo._to_entity(rec)

        # ✅ RESTORED: Exit price validation from v106
        if not exit_price.is_finite() or exit_price <= 0:
            raise ValueError("Exit price invalid.")

        is_system = reason in ["SL_HIT", "TP_HIT", "PARTIAL_FINAL", "AUTO_CLOSE_FINAL_TP"]
        if user_id and not is_system:
             user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
             if not user or rec.analyst_id != user.id: 
                 raise ValueError("Access Denied")

        rec.status = RecommendationStatusEnum.CLOSED
        rec.exit_price = exit_price
        rec.closed_at = datetime.now(timezone.utc)
        rec.open_size_percent = Decimal(0)
        rec.profit_stop_active = False

        close_event_data = {
            "price": float(exit_price),
            "reason": reason,
            "mode": "AUTO" if is_system else "MANUAL",
        }
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id,
            event_type="FINAL_CLOSE",
            event_data=close_event_data,
        ))
        await self._sync_tracked_trades_from_recommendation(
            db_session,
            rec,
            "SOURCE_CLOSED",
            close_event_data,
            mode="AUTO" if is_system else "MANUAL",
            title="🏁 Source Recommendation Closed",
        )
        
        if self.alert_service:
            await self.alert_service.remove_single_trigger("recommendation", rec.id)

        # ✅ FIXED: Added await (from v106)
        await self.notify_reply(
            rec.id,
            f"✅ Signal Closed at {_format_price(exit_price)}",
            db_session,
            operation="CLOSE",
        )
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=rebuild_alerts)
        return self.repo._to_entity(rec)

    async def partial_close_async(self, rec_id: int, user_id: str, close_percent: Decimal, 
                                  price: Decimal, db_session: Session, triggered_by: str = "MANUAL"):
        """✅ FIXED: Corrected Decimal comparison and actual_close logic from v106."""
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: 
            raise ValueError("Rec not found")
        if rec.status == RecommendationStatusEnum.CLOSED: 
            return self.repo._to_entity(rec)
        if rec.status != RecommendationStatusEnum.ACTIVE: 
            raise ValueError(f"Cannot close. Status is {rec.status.value}")

        if user_id:
             user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
             if not user or rec.analyst_id != user.id: 
                 raise ValueError("Access Denied")
            
        curr_pct = _to_decimal(rec.open_size_percent)
        # ✅ RESTORED: actual_close calculation from v106
        actual_close = min(_to_decimal(close_percent), curr_pct)
        
        rec.open_size_percent = curr_pct - actual_close
        pnl = _pct(rec.entry, price, rec.side)
        
        partial_event_data = {
            "price": float(price),
            "amount": float(actual_close),
            "pnl": pnl,
            "mode": triggered_by,
        }
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id,
            event_type="PARTIAL",
            event_data=partial_event_data,
        ))
        await self._sync_tracked_trades_from_recommendation(
            db_session,
            rec,
            "SOURCE_PARTIAL",
            partial_event_data,
            mode=triggered_by,
            title="💰 Source Partial Close",
        )
        
        # ✅ FIXED: Added await (from v106)
        await self.notify_reply(
            rec.id, 
            f"💰 Partial Close {actual_close:g}% at {_format_price(price)} (PnL: {pnl:.2f}%)", 
            db_session
        )
        
        # ✅ FIXED: Compare Decimal with Decimal, not float
        if rec.open_size_percent < Decimal('0.1'):
             return await self.close_recommendation_async(
                 rec.id, user_id, price, db_session, "PARTIAL_FINAL", rebuild_alerts=False
             )
        
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=False)
        return self.repo._to_entity(rec)

    # --- Recommendation Updates ---

    async def update_sl_for_user_async(self, rec_id: int, user_id: str, new_sl: Decimal, 
                                       db_session: Optional[Session] = None):
        if db_session is None: 
            with session_scope() as s: 
                return await self.update_sl_for_user_async(rec_id, user_id, new_sl, s)
        
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: 
            raise ValueError("Not found")
        if rec.status == RecommendationStatusEnum.CLOSED: 
            raise ValueError("Closed")

        try:
            targets_list = [
                {'price': _to_decimal(t.get('price')), 
                 'close_percent': t.get('close_percent', 0.0)} 
                for t in (rec.targets or [])
            ]
            _validate_recommendation_data(
                rec.side, _to_decimal(rec.entry), new_sl, 
                targets_list, is_breakeven_move=False
            )
        except ValueError as e:
            raise ValueError(f"Invalid SL: {e}")

        rec.stop_loss = new_sl
        sl_event_data = {"new": str(new_sl)}
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id,
            event_type="SL_UPDATED",
            event_data={**sl_event_data, "mode": "MANUAL"},
        ))
        await self._sync_tracked_trades_from_recommendation(
            db_session,
            rec,
            "SOURCE_SL_UPDATED",
            {"stop_loss": new_sl, **sl_event_data},
            mode="MANUAL",
        )
        
        # ✅ FIXED: Added await (from v106)
        await self.notify_reply(rec.id, f"⚠️ SL Updated to {_format_price(new_sl)}", db_session)
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)

    async def update_targets_for_user_async(self, rec_id: int, user_id: str, 
                                            new_targets: List[Dict], db_session: Session):
        """✅ FIXED: Restored validation from v106."""
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: 
            raise ValueError("Not found")
        if rec.status == RecommendationStatusEnum.CLOSED: 
            raise ValueError("Closed")
        
        # ✅ RESTORED: Validation logic from v106
        try:
            targets_validated = [
                {'price': _to_decimal(t['price']), 
                 'close_percent': t.get('close_percent', 0.0)} 
                for t in new_targets
            ]
            _validate_recommendation_data(
                rec.side, _to_decimal(rec.entry), 
                _to_decimal(rec.stop_loss), targets_validated
            )
        except ValueError as e:
            raise ValueError(f"Invalid Targets: {e}")
             
        rec.targets = [
            {'price': str(t['price']), 'close_percent': t['close_percent']}
            for t in targets_validated
        ]
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id,
            event_type="TP_UPDATED",
            event_data={"mode": "MANUAL"},
        ))
        await self._sync_tracked_trades_from_recommendation(
            db_session,
            rec,
            "SOURCE_TARGETS_UPDATED",
            {"targets": rec.targets},
            mode="MANUAL",
        )
        
        # ✅ FIXED: Added await (from v106)

        await self.notify_reply(rec.id, "🎯 Targets Updated", db_session)
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)
    
    async def update_entry_and_notes_async(self, rec_id: int, user_id: str, 
                                           new_entry: Optional[Decimal], new_notes: Optional[str], 
                                           db_session: Session):
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec: 
            raise ValueError("Not found")
        
        updated = False
        if new_entry is not None:
            if rec.status != RecommendationStatusEnum.PENDING: 
                raise ValueError("Entry only editable PENDING.")
            if new_entry <= 0: 
                raise ValueError("Entry must be positive")
            if rec.entry != new_entry:
                rec.entry = new_entry
                updated = True
        if new_notes is not None:
             rec.notes = new_notes
             updated = True
        
        if updated:
            db_session.add(RecommendationEvent(
                recommendation_id=rec.id, 
                event_type="DATA_UPDATED"
            ))
            # ✅ FIXED: Added await (from v106)
            await self.notify_reply(rec.id, "✏️ Data Updated", db_session)
            await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)
    
    async def set_exit_strategy_async(self, rec_id: int, user_id: str, mode: str, 
                                      price: Optional[Decimal] = None, 
                                      trailing_value: Optional[Decimal] = None, 
                                      active: bool = True, session: Optional[Session] = None):
        if session is None: 
            with session_scope() as s: 
                return await self.set_exit_strategy_async(
                    rec_id, user_id, mode, price, trailing_value, active, s
                )
        
        rec = self.repo.get_for_update(session, rec_id)
        if not rec: 
            raise ValueError("Not found")
        
        rec.profit_stop_mode = mode
        rec.profit_stop_active = active
        if price: 
            rec.profit_stop_price = price
        if trailing_value: 
            rec.profit_stop_trailing_value = trailing_value
        
        msg = f"📈 Strategy: {mode}" if active else "❌ Strategy Cancelled"
        
        # ✅ FIXED: Added await (from v106)
        await self.notify_reply(rec.id, msg, session)
        await self._commit_and_dispatch(session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)

    async def move_sl_to_breakeven_async(self, rec_id: int, db_session: Optional[Session] = None):
        if db_session is None: 
            with session_scope() as s: 
                return await self.move_sl_to_breakeven_async(rec_id, s)
        
        rec = self.repo.get_for_update(db_session, rec_id)
        if not rec or rec.status != RecommendationStatusEnum.ACTIVE: 
            raise ValueError("Only ACTIVE trades.")
            
        entry = _to_decimal(rec.entry)
        buffer = entry * Decimal('0.0005') 
        new_sl = entry + buffer if rec.side == 'LONG' else entry - buffer
        
        try:
            targets_list = [
                {'price': _to_decimal(t.get('price')), 
                 'close_percent': t.get('close_percent', 0.0)} 
                for t in (rec.targets or [])
            ]
            _validate_recommendation_data(
                rec.side, entry, new_sl, 
                targets_list, is_breakeven_move=True
            )
        except ValueError as e:
            raise ValueError(f"Cannot move to BE: {e}")

        rec.stop_loss = new_sl
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id, 
            event_type="SL_UPDATED", 
            event_data={"reason": "BreakEven", "new": str(new_sl), "mode": "MANUAL"}
        ))
        
        # ✅ FIXED: Added await (from v106)
        await self.notify_reply(rec.id, f"🛡️ Moved to Break-Even: {_format_price(new_sl)}", db_session)
        await self._commit_and_dispatch(db_session, rec, rebuild_alerts=True)
        return self.repo._to_entity(rec)

    # --- Event Processors (System) ---

    async def process_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
        with session_scope() as s:
            rec_orm = self.repo.get_for_update(s, item_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.ACTIVE: 
                return
            
            event_type = f"TP{target_index}_HIT"
            if any(e.event_type == event_type for e in (rec_orm.events or [])): 
                return
            
            s.add(RecommendationEvent(
                recommendation_id=rec_orm.id, 
                event_type=event_type,
                event_data={"price": float(price), "mode": "AUTO"},
            ))
            
            # ✅ FIXED: Added await (from v106)
            await self.notify_reply(
                rec_orm.id, 
                f"🎯 Hit TP{target_index} at {_format_price(price)}!", 
                db_session=s
            )
            s.flush()

            try: 
                target_info = rec_orm.targets[target_index - 1]
            except: 
                target_info = {}
            
            close_percent = _to_decimal(target_info.get("close_percent", 0))
            analyst_uid = str(rec_orm.analyst.telegram_user_id) if rec_orm.analyst else None
            
            if analyst_uid and close_percent > 0:
                await self.partial_close_async(
                    rec_orm.id, analyst_uid, close_percent, price, s, triggered_by="AUTO"
                )
            
            rec_orm = self.repo.get(s, item_id)
            if not rec_orm: 
                return 

            is_final = (target_index == len(rec_orm.targets or []))
            should_close = (rec_orm.exit_strategy == ExitStrategyEnum.CLOSE_AT_FINAL_TP and is_final)
            
            if (should_close or rec_orm.open_size_percent < Decimal('0.1')) and rec_orm.status == RecommendationStatusEnum.ACTIVE:
                 await self.close_recommendation_async(
                     rec_orm.id, analyst_uid, price, s, "AUTO_FINAL", rebuild_alerts=False
                 )
            elif close_percent <= 0:
                await self._commit_and_dispatch(s, rec_orm, rebuild_alerts=False)

    async def process_sl_hit_event(self, item_id: int, price: Decimal):
         with session_scope() as s:
             rec = self.repo.get_for_update(s, item_id)
             if rec and rec.status == RecommendationStatusEnum.ACTIVE:
                 await self.close_recommendation_async(
                     rec.id, None, price, s, "SL_HIT", rebuild_alerts=False
                 )

    async def process_activation_event(self, item_id: int):
        with session_scope() as s:
             rec = self.repo.get_for_update(s, item_id)
             if rec and rec.status == RecommendationStatusEnum.PENDING:
                 rec.status = RecommendationStatusEnum.ACTIVE
                 rec.activated_at = datetime.now(timezone.utc)
                 s.add(RecommendationEvent(
                     recommendation_id=rec.id, 
                         event_type="ACTIVATED",
                         event_data={"mode": "AUTO"},
                     ))

                 # ✅ FIXED: Added await (from v106)
                 await self.notify_reply(rec.id, f"▶️ ACTIVE!", db_session=s)
                 await self._commit_and_dispatch(s, rec, rebuild_alerts=True)

    async def process_invalidation_event(self, item_id: int):
         with session_scope() as s:
             rec = self.repo.get_for_update(s, item_id)
             if rec and rec.status == RecommendationStatusEnum.PENDING:
                 rec.status = RecommendationStatusEnum.CLOSED
                 rec.closed_at = datetime.now(timezone.utc)
                 s.add(RecommendationEvent(
                     recommendation_id=rec.id, 
                     event_type="INVALIDATED",
                     event_data={"mode": "AUTO"},
                 ))
                 
                 # ✅ FIXED: Added await (from v106)
                 await self.notify_reply(rec.id, f"❌ Invalidated", db_session=s)
                 
                 if self.alert_service: 
                     await self.alert_service.remove_single_trigger("recommendation", rec.id)
                 
                 await self._commit_and_dispatch(s, rec, rebuild_alerts=False)

    # --- UserTrade Lifecycle ---
    
    async def process_user_trade_activation_event(self, item_id: int):
        with session_scope() as s:
            trade = s.query(UserTrade).options(selectinload(UserTrade.events)).filter(
                UserTrade.id == item_id
            ).with_for_update().first()
            
            if trade and trade.status in (
                UserTradeStatusEnum.PENDING_ACTIVATION,
                UserTradeStatusEnum.WATCHLIST,
            ):
                trade.status = UserTradeStatusEnum.ACTIVATED
                trade.activated_at = datetime.now(timezone.utc)
                s.add(UserTradeEvent(
                    user_trade_id=trade.id,
                    event_type="ACTIVATED",
                    event_data={"mode": "AUTO"},
                ))
                await self._notify_trade_event(
                    s,
                    trade,
                    "▶️ Trade Activated",
                    f"Asset: <b>#{trade.asset}</b>",
                    mode="AUTO",
                )
                await self._commit_and_dispatch(s, trade, rebuild_alerts=True)

    async def process_user_trade_invalidation_event(self, item_id: int, price: Decimal):
         with session_scope() as s:
            trade = s.query(UserTrade).filter(UserTrade.id == item_id).with_for_update().first()
            if trade and trade.status in [UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.WATCHLIST]:
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.open_size_percent = Decimal("0")
                trade.closed_at = datetime.now(timezone.utc)
                s.add(UserTradeEvent(
                    user_trade_id=trade.id,
                    event_type="INVALIDATED",
                    event_data={"price": str(price), "mode": "AUTO"}
                ))
                
                if self.alert_service: 
                    await self.alert_service.remove_single_trigger("user_trade", item_id)
                
                await self._notify_trade_event(
                    s,
                    trade,
                    "❌ Trade Invalidated",
                    f"Price: <code>{_format_price(price)}</code>",
                    mode="AUTO",
                )
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)

    async def process_user_trade_sl_hit_event(self, item_id: int, price: Decimal):
         with session_scope() as s:
            trade = s.query(UserTrade).filter(UserTrade.id == item_id).with_for_update().first()
            if trade and trade.status == UserTradeStatusEnum.ACTIVATED:
                pnl = _pct(trade.entry, price, trade.side)
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.pnl_percentage = Decimal(str(pnl))
                trade.open_size_percent = Decimal("0")
                trade.closed_at = datetime.now(timezone.utc)
                s.add(UserTradeEvent(
                    user_trade_id=trade.id,
                    event_type="SL_HIT",
                    event_data={"price": str(price), "pnl": pnl, "mode": "AUTO"}
                ))
                
                if self.alert_service: 
                    await self.alert_service.remove_single_trigger("user_trade", item_id)
                
                await self._notify_trade_event(
                    s,
                    trade,
                    "🛑 Stop Loss Hit",
                    f"Asset: <b>#{trade.asset}</b> · Price: <code>{_format_price(price)}</code> · PnL: <b>{pnl:.2f}%</b>",
                    mode="AUTO",
                )
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)

    async def process_user_trade_tp_hit_event(self, item_id: int, target_index: int, price: Decimal):
         with session_scope() as s:
            trade = s.query(UserTrade).options(selectinload(UserTrade.events)).filter(
                UserTrade.id == item_id
            ).with_for_update().first()
            
            if not trade or trade.status != UserTradeStatusEnum.ACTIVATED: 
                return
            
            event_type = f"TP{target_index}_HIT"
            if any(e.event_type == event_type for e in (trade.events or [])): 
                return
            
            try:
                target_info = trade.targets[target_index - 1]
            except (IndexError, TypeError):
                target_info = {}
            close_percent = _to_decimal(target_info.get("close_percent", 0))
            trade.open_size_percent = max(
                Decimal("0"),
                _to_decimal(trade.open_size_percent) - close_percent,
            )
            s.add(UserTradeEvent(
                user_trade_id=trade.id,
                event_type=event_type,
                event_data={
                    "price": str(price),
                    "amount": float(close_percent),
                    "mode": "AUTO",
                },
            ))
            
            await self._notify_trade_event(
                s,
                trade,
                f"🎯 TP{target_index} Hit",
                f"Asset: <b>#{trade.asset}</b> · Price: <code>{_format_price(price)}</code> · Closed: <b>{close_percent:g}%</b>",
                mode="AUTO",
            )
            
            if target_index == len(trade.targets or []) or trade.open_size_percent < Decimal("0.1"):
                pnl = _pct(trade.entry, price, trade.side)
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.pnl_percentage = Decimal(str(pnl))
                trade.open_size_percent = Decimal("0")
                trade.closed_at = datetime.now(timezone.utc)
                
                if self.alert_service:
                    await self.alert_service.remove_single_trigger("user_trade", item_id)
                
                await self._notify_trade_event(
                    s,
                    trade,
                    "🏆 Final Target Hit",
                    f"Asset: <b>#{trade.asset}</b> · Price: <code>{_format_price(price)}</code> · PnL: <b>{pnl:.2f}%</b>",
                    mode="AUTO",
                )
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)
            else:
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)

    async def close_user_trade_async(self, user_id: str, trade_id: int, exit_price: Decimal, 
                                     db_session: Session) -> Optional[UserTrade]:
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user: 
            raise ValueError("User not found.")
        
        trade = db_session.query(UserTrade).filter(
            UserTrade.id == trade_id, 
            UserTrade.user_id == user.id
        ).with_for_update().first()
        
        if not trade: 
            raise ValueError(f"Trade #{trade_id} not found")
        
        if trade.status == UserTradeStatusEnum.CLOSED: 
            return trade
        if trade.status != UserTradeStatusEnum.ACTIVATED:
            raise ValueError("Only an activated UserTrade can be closed at a market price")
        
        pnl = 0.0
        if trade.status == UserTradeStatusEnum.ACTIVATED:
             pnl = _pct(trade.entry, exit_price, trade.side)
        
        trade.status = UserTradeStatusEnum.CLOSED
        trade.close_price = exit_price
        trade.pnl_percentage = Decimal(str(pnl))
        trade.open_size_percent = Decimal("0")
        trade.closed_at = datetime.now(timezone.utc)
        
        db_session.add(UserTradeEvent(
            user_trade_id=trade.id, 
            event_type="MANUAL_CLOSE",
            event_data={"price": str(exit_price), "pnl": pnl, "mode": "MANUAL"}
        ))
        
        if self.alert_service:
            await self.alert_service.remove_single_trigger("user_trade", trade.id)
            
        await self._notify_trade_event(
            db_session,
            trade,
            "✋ Trade Closed Manually",
            f"Asset: <b>#{trade.asset}</b> · Exit: <code>{_format_price(exit_price)}</code> · PnL: <b>{pnl:.2f}%</b>",
            mode="MANUAL",
        )
        await self._commit_and_dispatch(db_session, trade, rebuild_alerts=False)
        return trade

    async def cancel_pending_user_trade_async(self, user_id: str, trade_id: int, db_session: Session) -> Optional[UserTrade]:
        """Cancel a watchlist or pending-activation record without a market exit or PnL."""
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user:
            raise ValueError("User not found.")
        trade = db_session.query(UserTrade).filter(
            UserTrade.id == trade_id,
            UserTrade.user_id == user.id,
        ).with_for_update().first()
        if not trade:
            raise ValueError(f"Trade #{trade_id} not found")
        if trade.status == UserTradeStatusEnum.CANCELLED:
            return trade
        if trade.status not in (UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION):
            raise ValueError("Only a pending UserTrade can be cancelled")

        prior_status = trade.status.value
        trade.status = UserTradeStatusEnum.CANCELLED
        trade.close_price = None
        trade.pnl_percentage = None
        trade.open_size_percent = Decimal("0")
        trade.closed_at = datetime.now(timezone.utc)
        db_session.add(UserTradeEvent(
            user_trade_id=trade.id,
            event_type="PENDING_CANCELLED",
            event_data={"mode": "MANUAL", "prior_status": prior_status, "reason": "USER_CANCELLED_BEFORE_ACTIVATION"},
        ))
        if self.alert_service:
            await self.alert_service.remove_single_trigger("user_trade", trade.id)
        await self._notify_trade_event(
            db_session,
            trade,
            "🚫 Pending Trade Cancelled",
            f"Asset: <b>#{trade.asset}</b> · No market entry or PnL was recorded.",
            mode="MANUAL",
        )
        await self._commit_and_dispatch(db_session, trade, rebuild_alerts=False)
        return trade

# --- END OF PRODUCTION READY FILE ---
