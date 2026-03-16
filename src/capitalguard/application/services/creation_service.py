# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/creation_service.py ---
# File: src/capitalguard/application/services/creation_service.py
# Version: v5.0.0-GOLD (Atomic Shadow Fix & Full Resilience)
# ✅ THE FIX: 
#    1. Forced SQL Update: Bypasses ORM session cache to ensure 'is_shadow=False' sticks.
#    2. Decoupled Fate: Telegram errors no longer kill the trade activation.
#    3. Immediate Commit: Data becomes real BEFORE indexing.

from __future__ import annotations
import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session
from sqlalchemy import select, text

# Infrastructure & Domain Imports
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    Recommendation, RecommendationEvent, User,
    RecommendationStatusEnum, UserTrade,
    OrderTypeEnum, ExitStrategyEnum,
    UserTradeStatusEnum,
    WatchedChannel,
    PublishedMessage
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
# Type-only imports
if False:
    from .alert_service import AlertService
    from .price_service import PriceService
    from .market_data_service import MarketDataService
    from .lifecycle_service import LifecycleService

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

def _parse_int_user_id(user_id: Any) -> Optional[int]:
    try:
        if user_id is None: return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.lstrip('-').isdigit() else None
    except: return None

# --- Service Class ---

class CreationService:
    """
    R2 Service - المسؤول عن الولادة الآمنة للصفقات وإنشائها.
    """
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
        # Circular dependencies injected later via boot.py
        self.alert_service: Optional["AlertService"] = None
        self.lifecycle_service: Optional["LifecycleService"] = None

    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        """التحقق الصارم من صحة البيانات المالية قبل الحفظ."""
        side_upper = (str(side) or "").upper()
        
        # 1. التحقق من وجود الأرقام وصحتها
        if not all(v is not None and isinstance(v, Decimal) and v.is_finite() and v > 0 for v in [entry, stop_loss]):
            raise ValueError("Entry and SL must be positive finite Decimals.")
        
        if not targets: 
            raise ValueError("Targets must be a non-empty list.")
        
        target_prices = []
        for i, t in enumerate(targets):
            price = _to_decimal(t.get('price'))
            if price <= 0: 
                raise ValueError(f"Target {i+1} price invalid (must be > 0).")
            target_prices.append(price)

        # 2. التحقق من المنطق المالي (Long vs Short)
        if side_upper == "LONG":
            if stop_loss >= entry: raise ValueError("LONG SL must be < Entry.")
            if any(p <= entry for p in target_prices): raise ValueError("LONG targets must be > Entry.")
        elif side_upper == "SHORT":
            if stop_loss <= entry: raise ValueError("SHORT SL must be > Entry.")
            if any(p >= entry for p in target_prices): raise ValueError("SHORT targets must be < Entry.")
        else:
            raise ValueError("Side must be LONG or SHORT.")
        
        risk = abs(entry - stop_loss)
        if risk.is_zero():
            raise ValueError("Entry and SL cannot be equal.")

    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_db_id: int, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """نشر التوصية إلى قنوات تليجرام."""
        report = {"success": [], "failed": []}
        
        # جلب القنوات المرتبطة بالمحلل
        channels = ChannelRepository(session).list_by_analyst(user_db_id, only_active=True)
        if target_channel_ids: 
            channels = [c for c in channels if c.telegram_channel_id in target_channel_ids]
        
        if not channels:
            report["failed"].append({"reason": "No active channels linked/selected."})
            return rec_entity, report

        # استيراد الكيبورد ديناميكياً لتجنب مشاكل التبعية
        try:
            from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        except ImportError:
            public_channel_keyboard = lambda *_: None
        
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        
        # دالة داخلية للنشر الآمن
        async def _send(ch_id):
            try:
                return await self._call_notifier_maybe_async(self.notifier.post_to_channel, ch_id, rec_entity, keyboard)
            except Exception as e: 
                return e

        # النشر المتوازي لجميع القنوات
        tasks = [asyncio.create_task(_send(ch.telegram_channel_id)) for ch in channels]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, ch in enumerate(channels):
            res = results[i]
            if isinstance(res, tuple) and len(res) == 2:
                # حفظ رسالة القناة في قاعدة البيانات للرجوع إليها لاحقاً (للتعديل/الإغلاق)
                session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=res[0], telegram_message_id=res[1]))
                report["success"].append({"channel_id": ch.telegram_channel_id})
            else:
                report["failed"].append({"channel_id": ch.telegram_channel_id, "error": str(res)})
        
        session.flush()
        return rec_entity, report

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """استدعاء دالة الإشعار سواء كانت متزامنة أو غير متزامنة."""
        if inspect.iscoroutinefunction(fn): 
            return await fn(*args, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args, **kwargs)

    async def _notify_user_trade_update(self, user_id: int, text: str):
        """إشعار المحلل بحالة النشر."""
        try:
            with session_scope() as session:
                user = UserRepository(session).find_by_id(user_id)
                if user: 
                    await self._call_notifier_maybe_async(self.notifier.send_private_text, chat_id=user.telegram_user_id, text=text)
        except: pass

    # --- MAIN ENTRY POINT (Analyst Creator) ---
    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """
        Lightweight Creator: التحقق + الحفظ كظل + العودة فوراً.
        هذه الدالة سريعة جداً لضمان استجابة الواجهة.
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user or user.user_type != UserTypeEntity.ANALYST:
            raise ValueError("Only analysts can create recommendations.")
        
        # تحضير البيانات
        entry = _to_decimal(kwargs['entry'])
        sl = _to_decimal(kwargs['stop_loss'])
        targets = [{'price': _to_decimal(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in kwargs['targets']]
        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        order_type = OrderTypeEnum[kwargs['order_type'].upper()]
        
        # منطق تحديد الحالة الأولية (Status Logic)
        if order_type == OrderTypeEnum.MARKET:
            # في حالة الماركت، نجلب السعر الحالي ونفعلها فوراً
            live = await self.price_service.get_cached_price(asset, market, True)
            status, final_entry = RecommendationStatusEnum.ACTIVE, _to_decimal(live) if live else None
            if not final_entry or final_entry <= 0: 
                # إذا فشل جلب السعر، نستخدم السعر المدخل يدوياً كاحتياط، أو نرفض
                if entry > 0:
                     status, final_entry = RecommendationStatusEnum.ACTIVE, entry
                else:
                     raise RuntimeError("Invalid live price and no manual entry provided.")
        else:
            status, final_entry = RecommendationStatusEnum.PENDING, entry
        
        # التحقق النهائي
        self._validate_recommendation_data(side, final_entry, sl, targets)
        
        # تحويل الأهداف لـ JSON
        targets_db = [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in targets]
        
        # إنشاء الكائن (OR)
        rec = Recommendation(
            analyst_id=user.id, asset=asset, side=side, entry=final_entry, stop_loss=sl, 
            targets=targets_db, order_type=order_type, status=status, market=market, 
            notes=kwargs.get('notes'), exit_strategy=ExitStrategyEnum.CLOSE_AT_FINAL_TP,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None,
            is_shadow=True # ✅ Start as Shadow (الحفظ كظل)
        )
        
        db_session.add(rec)
        db_session.flush()
        
        # تسجيل حدث الإنشاء
        db_session.add(RecommendationEvent(recommendation_id=rec.id, event_type="CREATED_ACTIVE" if status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING", event_data={'entry': str(final_entry)}))
        
        db_session.flush()
        db_session.refresh(rec)
        return self.repo._to_entity(rec), {}

    # --- ROBUST BACKGROUND TASK (The Fix) ---
    async def background_publish_and_index(self, rec_id: int, user_db_id: int, target_channel_ids: Optional[Set[int]] = None):
        """
        [Background Task - FINAL ATOMIC VERSION]
        الهدف: ضمان التثبيت الحقيقي في قاعدة البيانات وعزل أخطاء تليجرام.
        """
        logger.info(f"[BG Rec {rec_id}]: Starting background process...")
        
        # تجهيز الخدمات (Fail-safe)
        if not self.alert_service:
            logger.critical(f"[BG Rec {rec_id}]: AlertService missing. Proceeding with DB update.")

        try:
            with session_scope() as session:
                # 1. التحقق من وجود الصفقة
                rec_orm = self.repo.get(session, rec_id)
                if not rec_orm:
                    logger.error(f"[BG Rec {rec_id}]: ORM object not found.")
                    return

                # 2. النشر (Publishing) - محاولة معزولة
                rec_entity = self.repo._to_entity(rec_orm)
                success_count = 0
                publish_error = None

                if rec_entity:
                    try:
                        # النشر للقنوات
                        _, report = await self._publish_recommendation(
                            session, rec_entity, user_db_id, target_channel_ids
                        )
                        success_count = len(report.get("success", []))
                        logger.info(f"[BG Rec {rec_id}]: Published to {success_count} channels.")
                        
                        # حفظ معرفات الرسائل المنشورة في نفس الجلسة
                        session.flush() 
                    except Exception as e:
                        publish_error = str(e)
                        logger.error(f"[BG Rec {rec_id}]: Publishing partial failure: {e}")

                # 3. التثبيت وإزالة الظل (Atomic Commit) - الخطوة الأهم
                try:
                    # حفظ الحالة الحالية للعرض
                    status_str = rec_orm.status.value

                    # فرض التحديث المباشر باستخدام SQL الخام لتجاوز أي مشاكل في الـ ORM Session Cache
                    session.execute(
                        text("UPDATE recommendations SET is_shadow = :val WHERE id = :rid"),
                        {"val": False, "rid": rec_id}
                    )
                    session.commit() # ✅ تثبيت إجباري (Force Commit)
                    logger.info(f"[BG Rec {rec_id}]: FORCE COMMITTED (is_shadow=False).")
                
                except Exception as e:
                    logger.critical(f"[BG Rec {rec_id}]: FATAL DB ERROR during commit: {e}")
                    return # لا نرسل رسالة نجاح إذا فشل التثبيت

                # 4. الفهرسة (Indexing) - بعد التأكد من التثبيت
                # هذه الخطوة تعيد قراءة البيانات النظيفة من قاعدة البيانات
                if self.alert_service:
                    try:
                        # إعادة جلب الكائن لضمان البيانات الطازجة في جلسة جديدة (أو نفس الجلسة المجددة)
                        rec_orm_fresh = self.repo.get(session, rec_id) 
                        trigger_data = self.alert_service.build_trigger_data_from_orm(rec_orm_fresh)
                        if trigger_data:
                            await self.alert_service.add_trigger_data(trigger_data)
                            logger.info(f"[BG Rec {rec_id}]: Added to Monitoring Index.")
                    except Exception as e:
                        logger.error(f"[BG Rec {rec_id}]: Indexing failed (Non-fatal): {e}")

                # 5. إشعار المحلل (فقط إذا نجح التثبيت)
                try:
                    state_emoji = "▶️" if status_str == "ACTIVE" else "⏳"
                    msg = f"✅ **تم التثبيت بنجاح!**\nالصفقة #{rec_orm.asset} أصبحت حقيقية.\nالحالة: {state_emoji} **{status_str}**"
                    
                    if publish_error:
                        msg += f"\n⚠️ تنبيه: فشل النشر في القنوات ({publish_error})."
                    elif success_count == 0:
                        msg += "\nℹ️ لم يتم النشر في أي قناة (ربما لا توجد قنوات)، لكن الصفقة تعمل."
                    
                    await self._notify_user_trade_update(user_id=user_db_id, text=msg)
                except: pass

        except Exception as e:
            logger.error(f"[BG Rec {rec_id}]: UNHANDLED CRASH: {e}", exc_info=True)

    # --- Public API - Create Trade (Trader) ---
    async def create_trade_from_forwarding_async(
        self, 
        user_id: str, 
        trade_data: Dict[str, Any], 
        original_text: Optional[str], 
        db_session: Session,
        status_to_set: str, 
        original_published_at: Optional[datetime],
        channel_info: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Core Algorithm - R1: Trader Copy/Forward"""
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}
        
        try:
            entry_dec = trade_data['entry']
            sl_dec = trade_data['stop_loss']
            targets_list_validated = trade_data['targets']
            self._validate_recommendation_data(trade_data['side'], entry_dec, sl_dec, targets_list_validated)
            targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_validated]

            watched_channel = None
            if channel_info and channel_info.get('id'):
                channel_tg_id = channel_info['id']
                stmt = select(WatchedChannel).filter_by(
                    user_id=trader_user.id, 
                    telegram_channel_id=channel_tg_id
                )
                watched_channel = db_session.execute(stmt).scalar_one_or_none()
                if not watched_channel:
                    logger.info(f"Creating new WatchedChannel '{channel_info.get('title')}' for user {trader_user.id}")
                    watched_channel = WatchedChannel(
                        user_id=trader_user.id,
                        telegram_channel_id=channel_tg_id,
                        channel_title=channel_info.get('title'),
                        is_active=True
                    )
                    db_session.add(watched_channel)
                    db_session.flush() 

            new_trade = UserTrade(
                user_id=trader_user.id,
                asset=trade_data['asset'],
                side=trade_data['side'],
                entry=entry_dec,
                stop_loss=sl_dec,
                targets=targets_for_db,
                status=UserTradeStatusEnum[status_to_set],
                source_forwarded_text=original_text,
                original_published_at=original_published_at,
                watched_channel_id=watched_channel.id if watched_channel else None,
                activated_at=None 
            )
            db_session.add(new_trade)
            db_session.flush()
            
            if self.alert_service:
                db_session.refresh(new_trade, attribute_names=['user'])
                trigger_data = self.alert_service.build_trigger_data_from_orm(new_trade)
                if trigger_data:
                    await self.alert_service.add_trigger_data(trigger_data)
                else:
                    logger.error(f"Failed to build trigger data for new UserTrade {new_trade.id}")
            
            logger.info(f"UserTrade {new_trade.id} created for user {user_id} with status {status_to_set}.")
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
        """Core Algorithm: Trader Activate Rec"""
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user: return {'success': False, 'error': 'User not found'}
        
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm: return {'success': False, 'error': 'Signal not found'}

        existing_trade = self.repo.find_user_trade_by_source_id(db_session, trader_user.id, rec_id)
        if existing_trade:
            return {'success': False, 'error': 'You are already tracking this signal.'}
        
        try:
            rec_status = rec_orm.status
            if rec_status == RecommendationStatusEnum.PENDING:
                user_trade_status = UserTradeStatusEnum.PENDING_ACTIVATION
                user_trade_activated_at = None
            elif rec_status == RecommendationStatusEnum.ACTIVE:
                user_trade_status = UserTradeStatusEnum.ACTIVATED
                user_trade_activated_at = rec_orm.activated_at or datetime.now(timezone.utc)
            else: # CLOSED
                return {'success': False, 'error': 'This signal is already closed.'}

            new_trade = UserTrade( 
                user_id=trader_user.id, 
                asset=rec_orm.asset, 
                side=rec_orm.side, 
                entry=rec_orm.entry, 
                stop_loss=rec_orm.stop_loss, 
                targets=rec_orm.targets, 
                status=user_trade_status, 
                activated_at=user_trade_activated_at, 
                original_published_at=rec_orm.created_at, 
                source_recommendation_id=rec_orm.id 
            )
            db_session.add(new_trade)
            db_session.flush()
            
            if self.alert_service:
                db_session.refresh(new_trade, attribute_names=['user'])
                trigger_data = self.alert_service.build_trigger_data_from_orm(new_trade)
                if trigger_data:
                    await self.alert_service.add_trigger_data(trigger_data)

            logger.info(f"UserTrade {new_trade.id} created user {user_id} tracking Rec {rec_id} with status {user_trade_status.value}.")
            return {'success': True, 'trade_id': new_trade.id, 'asset': new_trade.asset}
        
        except Exception as e:
            logger.error(f"Error create trade from rec user {user_id}, rec {rec_id}: {e}", exc_info=True)
            db_session.rollback()
            return {'success': False, 'error': 'Internal error tracking signal.'}
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/creation_service.py ---