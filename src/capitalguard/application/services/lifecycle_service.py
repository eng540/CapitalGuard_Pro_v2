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
import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import Session
from sqlalchemy import select, text
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

# [STEP-6A] Audit Logger — سجل لا يُحذف لكل حدث مالي
from capitalguard.infrastructure.audit.audit_logger import (
    log_trade_event,
    log_tp_hit,
    log_sl_hit,
    log_recommendation_closed,
    log_activation,
)

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
    def __init__(self, repo: RecommendationRepository, notifier: Any):
        self.repo = repo
        self.notifier = notifier
        self.alert_service: Optional["AlertService"] = None

    # --- Internal Core Methods ---
    async def _commit_and_dispatch(
        self,
        session: Session,
        obj: Any,
        rebuild_alerts: bool = False,   # [STEP-5B] كان True — غُيِّر لـ False
    ):
        """
        [STEP-5B] يُنفِّذ commit ويُطلق إشعارات الحالة.

        تغيير rebuild_alerts الافتراضي: True → False

        المشكلة السابقة:
            rebuild_alerts=True في كل استدعاء = full DB scan بعد كل حدث.
            مع 50 توصية نشطة و 500+ حدث/يوم = 500+ full table scans.
            كل scan = SELECT * من جداول recommendation_events, user_trade_events.

        الاستراتيجية الجديدة (STEP-5B):
            ✅ كل إغلاق توصية → remove_single_trigger() مسبقاً (O(n) في الذاكرة)
            ✅ كل توصية جديدة → add_trigger_data() (O(1), لا DB)
            ✅ كل حدث (TP/SL/Activation) → mark_event_processed() (O(n) في الذاكرة)
            ✅ Full rebuild: فقط عند _run_index_sync() كل 10 دقائق (شبكة الأمان)
                            + عند startup و watchdog restart

        متى يظل rebuild_alerts=True مبرَّراً (استثنائي صريح):
            - process_activation_event: إعادة تصنيف التوصية PENDING→ACTIVE
              يتطلب تحديث الـ entry/active في الـ index
            - update_sl_for_recommendation: بيانات SL تغيّرت في الـ trigger dict

        المعيار المالي:
            في أنظمة التداول، العمليات على المسار الحرج (critical path)
            يجب أن تكون O(1) أو O(log n).
            Full DB scan = O(n) حيث n = عدد التوصيات النشطة = غير مقبول.
        """
        try:
            session.commit()

            # ✅ منع DetachedInstanceError (من v200)
            try:
                session.refresh(obj)
            except Exception:
                pass  # الكائن قد يكون مُحذوفاً أو حالته غير صالحة

            # [STEP-5B] full rebuild فقط عند الحاجة الصريحة
            if rebuild_alerts and self.alert_service:
                await self.alert_service.build_triggers_index()

            if isinstance(obj, Recommendation):
                entity = self.repo._to_entity(obj)
                if entity:
                    await self.notify_card_update(entity, session)

        except Exception as e:
            logger.error(f"Commit dispatch failed: {e}", exc_info=True)
            session.rollback()
            raise  # ✅ re-raise لإبلاغ المستدعي (من v106)

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
        
        async def _upd(ch_id, msg_id):
            if inspect.iscoroutinefunction(self.notifier.edit_recommendation_card_by_ids):
                await self.notifier.edit_recommendation_card_by_ids(ch_id, msg_id, rec_entity, bot_username)
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.notifier.edit_recommendation_card_by_ids, ch_id, msg_id, rec_entity, bot_username)

        await asyncio.gather(*[_upd(m.telegram_channel_id, m.telegram_message_id) for m in msgs], return_exceptions=True)

    async def notify_reply(self, rec_id: int, text: str, db_session: Session):
        """✅ FIXED: Added proper exception handling for async tasks."""
        msgs = self.repo.get_published_messages(db_session, rec_id)
        
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

    async def _notify_user_trade_update(self, user_id: int, text: str):
        try:
            with session_scope() as session:
                user = UserRepository(session).find_by_id(user_id)
                if user:
                    chat_id = user.telegram_user_id
                    if inspect.iscoroutinefunction(self.notifier.send_private_text):
                        await self.notifier.send_private_text(chat_id, text)
                    else:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, self.notifier.send_private_text, chat_id, text)
        except Exception as e:
            logger.error(f"Failed to notify user {user_id}: {e}")

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

        is_system = reason in ["SL_HIT", "TP_HIT", "PARTIAL_FINAL", "AUTO_CLOSE_FINAL_TP", 
                               "WEB_CLOSE", "WEB_PARTIAL", "MANUAL_PRICE_CLOSE"]
        if user_id and not is_system:
             user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
             if not user or rec.analyst_id != user.id: 
                 raise ValueError("Access Denied")

        rec.status = RecommendationStatusEnum.CLOSED
        rec.exit_price = exit_price
        rec.closed_at = datetime.now(timezone.utc)
        rec.open_size_percent = Decimal(0)
        rec.profit_stop_active = False

        db_session.add(RecommendationEvent(
            recommendation_id=rec.id, 
            event_type="FINAL_CLOSE", 
            event_data={"price": float(exit_price), "reason": reason}
        ))
        
        if self.alert_service:
            await self.alert_service.remove_single_trigger("recommendation", rec.id)

        # [STEP-6A] Audit Log — إغلاق لا يُحذف
        log_recommendation_closed(
            rec_id=rec.id,
            price=exit_price,
            reason=reason,
            triggered_by="SYSTEM" if is_system else "MANUAL",
            symbol=getattr(rec, "symbol", None),
        )

        # ✅ FIXED: Added await (from v106)
        await self.notify_reply(rec.id, f"✅ Signal Closed at {_format_price(exit_price)}", db_session)
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
        
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id, 
            event_type="PARTIAL", 
            event_data={"price": float(price), "amount": float(actual_close), "pnl": pnl}
        ))
        
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
        db_session.add(RecommendationEvent(
            recommendation_id=rec.id, 
            event_type="SL_UPDATED", 
            event_data={"new": str(new_sl)}
        ))
        
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
            event_type="TP_UPDATED"
        ))
        
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
            event_data={"reason": "BreakEven", "new": str(new_sl)}
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
                event_data={"price": float(price)}
            ))

            # [STEP-0A] تحديث الذاكرة فوراً بعد كتابة الحدث لـ DB.
            # يمنع إعادة التقييم في كل تيك حتى rebuild_triggers_index.
            if self.alert_service:
                await self.alert_service.mark_event_processed(
                    "recommendation", item_id, event_type
                )

            # [STEP-6A] Audit Log — TP hit لا يُحذف
            log_tp_hit(
                item_type="recommendation",
                item_id=item_id,
                target_index=target_index,
                price=price,
                symbol=getattr(rec_orm, "symbol", None),
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
                     event_type="ACTIVATED"
                 ))

                 # [STEP-0A] تحديث الذاكرة فوراً
                 if self.alert_service:
                     await self.alert_service.mark_event_processed(
                         "recommendation", item_id, "ACTIVATED"
                     )

                 # [STEP-6A] Audit Log
                 log_activation(
                     item_type="recommendation",
                     item_id=item_id,
                     symbol=getattr(rec, "symbol", None),
                 )

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
                     event_type="INVALIDATED"
                 ))

                 # [STEP-0A] تحديث الذاكرة فوراً
                 if self.alert_service:
                     await self.alert_service.mark_event_processed(
                         "recommendation", item_id, "INVALIDATED"
                     )

                 # [STEP-6A] Audit Log
                 log_trade_event(
                     event_type="INVALIDATION",
                     item_type="recommendation",
                     item_id=item_id,
                     reason="SL_HIT_WHILE_PENDING",
                     triggered_by="SYSTEM",
                     symbol=getattr(rec, "symbol", None),
                 )

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

            if trade and trade.status == UserTradeStatusEnum.PENDING_ACTIVATION:
                trade.status = UserTradeStatusEnum.ACTIVATED
                trade.activated_at = datetime.now(timezone.utc)
                s.add(UserTradeEvent(
                    user_trade_id=trade.id,
                    event_type="ACTIVATED"
                ))

                # [STEP-0A] تحديث الذاكرة فوراً
                if self.alert_service:
                    await self.alert_service.mark_event_processed(
                        "user_trade", item_id, "ACTIVATED"
                    )

                await self._notify_user_trade_update(
                    trade.user_id,
                    f"▶️ Trade #{trade.asset} Activated!"
                )
                await self._commit_and_dispatch(s, trade, rebuild_alerts=True)

    async def process_user_trade_invalidation_event(self, item_id: int, price: Decimal):
         with session_scope() as s:
            trade = s.query(UserTrade).filter(UserTrade.id == item_id).with_for_update().first()
            if trade and trade.status in [UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.WATCHLIST]:
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.closed_at = datetime.now(timezone.utc)
                s.add(UserTradeEvent(
                    user_trade_id=trade.id, 
                    event_type="INVALIDATED", 
                    event_data={"price": str(price)}
                ))
                
                if self.alert_service: 
                    await self.alert_service.remove_single_trigger("user_trade", item_id)
                
                await self._notify_user_trade_update(
                    trade.user_id, 
                    f"❌ Trade #{trade.asset} Invalidated"
                )
                await self._commit_and_dispatch(s, trade, rebuild_alerts=False)

    async def process_user_trade_sl_hit_event(self, item_id: int, price: Decimal):
         with session_scope() as s:
            trade = s.query(UserTrade).filter(UserTrade.id == item_id).with_for_update().first()
            if trade and trade.status == UserTradeStatusEnum.ACTIVATED:
                pnl = _pct(trade.entry, price, trade.side)
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.closed_at = datetime.now(timezone.utc)
                s.add(UserTradeEvent(
                    user_trade_id=trade.id, 
                    event_type="SL_HIT", 
                    event_data={"price": str(price), "pnl": pnl}
                ))
                
                if self.alert_service: 
                    await self.alert_service.remove_single_trigger("user_trade", item_id)
                
                await self._notify_user_trade_update(
                    trade.user_id, 
                    f"🛑 SL Hit #{trade.asset} @ {_format_price(price)} (PnL: {pnl:.2f}%)"
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

            s.add(UserTradeEvent(
                user_trade_id=trade.id,
                event_type=event_type,
                event_data={"price": str(price)}
            ))

            # [STEP-0A] تحديث الذاكرة فوراً
            if self.alert_service:
                await self.alert_service.mark_event_processed(
                    "user_trade", item_id, event_type
                )
            
            await self._notify_user_trade_update(
                trade.user_id, 
                f"🎯 TP{target_index} Hit #{trade.asset}"
            )
            
            if target_index == len(trade.targets or []):
                pnl = _pct(trade.entry, price, trade.side)
                trade.status = UserTradeStatusEnum.CLOSED
                trade.close_price = price
                trade.closed_at = datetime.now(timezone.utc)
                
                if self.alert_service: 
                    await self.alert_service.remove_single_trigger("user_trade", item_id)
                
                await self._notify_user_trade_update(
                    trade.user_id, 
                    f"🏆 Final Target Hit! PnL: {pnl:.2f}%"
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
        
        pnl = 0.0
        if trade.status == UserTradeStatusEnum.ACTIVATED:
             pnl = _pct(trade.entry, exit_price, trade.side)
        
        trade.status = UserTradeStatusEnum.CLOSED
        trade.close_price = exit_price
        trade.closed_at = datetime.now(timezone.utc)
        
        db_session.add(UserTradeEvent(
            user_trade_id=trade.id, 
            event_type="MANUAL_CLOSE", 
            event_data={"price": str(exit_price), "pnl": pnl}
        ))
        
        if self.alert_service:
            await self.alert_service.remove_single_trigger("user_trade", trade.id)
            
        await self._commit_and_dispatch(db_session, trade, rebuild_alerts=False)
        return trade

# --- END OF PRODUCTION READY FILE ---