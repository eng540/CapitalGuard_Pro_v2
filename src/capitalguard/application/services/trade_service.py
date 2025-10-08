# src/capitalguard/application/services/trade_service.py (v28.0 - FINAL PRODUCTION READY)
"""
TradeService - الإصدار النهائي الكامل والداعم لتعدد المستخدمين مع منطق "توصية الظل"
خدمة متكاملة لإدارة التوصيات والصفقات مع دعم كامل للغة العربية ومعالجة الأخطاء المحسنة.
"""

import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    PublishedMessage, Recommendation, RecommendationEvent, User, UserType,
    RecommendationStatusEnum, UserTrade, UserTradeStatus, OrderTypeEnum, ExitStrategyEnum
)
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository, ChannelRepository, UserRepository
)
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType,
    ExitStrategy
)
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets
from capitalguard.interfaces.telegram.ui_texts import _pct, _calculate_weighted_pnl

logger = logging.getLogger(__name__)

# ثوابت النظام
SYSTEM_USER_ID_FOR_FORWARDING = 0
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_BASE = 0.2  # ثواني

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """تحويل معرف المستخدم إلى عدد صحيح بشكل آمن"""
    try:
        if user_id is None:
            return None
        user_str = str(user_id).strip()
        return int(user_str) if user_str.isdigit() else None
    except (TypeError, ValueError, AttributeError):
        return None

class TradeService:
    """
    خدمة متكاملة لإدارة التوصيات والصفقات
    تدعم المحللين والمتداولين مع نظام موحد للعرض والإدارة
    """
    
    def __init__(
        self,
        repo: RecommendationRepository,
        notifier: Any,
        market_data_service: Any,
        price_service: Any,
        alert_service: Any,
    ):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service
        self.price_service = price_service
        self.alert_service = alert_service

    # ==================== دوال التحقق والصلاحيات ====================
    
    def _check_analyst_permission(self, user: User) -> bool:
        """التحقق من صلاحية المستخدم كمحلل"""
        return user and user.user_type == UserType.ANALYST

    def _check_trade_ownership(self, trade: UserTrade, user_id: int) -> bool:
        """التحقق من ملكية الصفقة للمستخدم"""
        return trade and trade.user_id == user_id

    def _check_recommendation_ownership(self, rec: Recommendation, user_id: int) -> bool:
        """التحقق من ملكية التوصية للمستخدم"""
        return rec and rec.analyst_id == user_id

    def _get_user_by_telegram_id(self, db_session: Session, telegram_id: str) -> Optional[User]:
        """الحصول على المستخدم بواسطة معرف التليجرام"""
        uid_int = _parse_int_user_id(telegram_id)
        if not uid_int:
            return None
        return UserRepository(db_session).find_by_telegram_id(uid_int)

    def _get_or_create_system_user(self, db_session: Session) -> User:
        """الحصول على أو إنشاء مستخدم النظام لتوصيات الظل"""
        system_user = db_session.query(User).filter(User.id == SYSTEM_USER_ID_FOR_FORWARDING).first()
        if not system_user:
            logger.info("إنشاء مستخدم النظام لتوصيات الظل - ID: %s", SYSTEM_USER_ID_FOR_FORWARDING)
            system_user = User(
                id=SYSTEM_USER_ID_FOR_FORWARDING, 
                telegram_user_id=-1, 
                username='system_forwarder', 
                user_type=UserType.ANALYST, 
                is_active=False
            )
            db_session.add(system_user)
            db_session.flush()
        return system_user

    # ==================== دوال الإشعارات والاتصالات ====================

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """استدعاء الدوال المتزامنة وغير المتزامنة بشكل آمن"""
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity):
        """تحديث بطاقة التوصية في القنوات مع معالجة الأخطاء"""
        messages_to_delete = []
        
        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec_entity.id)
            if not published_messages:
                logger.debug("لا توجد رسائل منشورة للتحديث للتوصية #%s", rec_entity.id)
                return
                
            logger.info("جاري تحديث %d بطاقة للتوصية #%s", len(published_messages), rec_entity.id)
            
            for msg_meta in published_messages:
                try:
                    edit_fn = getattr(self.notifier, "edit_recommendation_card_by_ids", None)
                    if not edit_fn:
                        logger.error("الدالة edit_recommendation_card_by_ids غير متوفرة في الإشعار")
                        continue
                        
                    await self._call_notifier_maybe_async(
                        edit_fn, 
                        channel_id=msg_meta.telegram_channel_id, 
                        message_id=msg_meta.telegram_message_id, 
                        rec=rec_entity
                    )
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    if any(phrase in error_msg for phrase in ["message to edit not found", "message not found"]):
                        logger.warning("الرسالة %s للتوصية %s غير موجودة - سيتم حذفها", 
                                     msg_meta.telegram_message_id, rec_entity.id)
                        messages_to_delete.append(msg_meta)
                    else:
                        logger.error("فشل تحديث البطاقة للتوصية %s في القناة %s: %s", 
                                   rec_entity.id, msg_meta.telegram_channel_id, e, exc_info=True)
            
            # حذف الرسائل غير الموجودة
            for msg in messages_to_delete:
                try:
                    session.delete(msg)
                    session.flush()
                except Exception:
                    logger.exception("فشل حذف سجل الرسالة المنشورة %s", getattr(msg, "id", "غير معروف"))

    def notify_reply(self, rec_id: int, text: str):
        """إرسال رد على التوصية في القنوات"""
        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec_id)
            for msg_meta in published_messages:
                try:
                    post_fn = getattr(self.notifier, "post_notification_reply", None)
                    if not post_fn:
                        logger.error("الدالة post_notification_reply غير متوفرة في الإشعار")
                        continue
                        
                    if inspect.iscoroutinefunction(post_fn):
                        try:
                            loop = asyncio.get_running_loop()
                            asyncio.run_coroutine_threadsafe(
                                post_fn(
                                    chat_id=msg_meta.telegram_channel_id, 
                                    message_id=msg_meta.telegram_message_id, 
                                    text=text
                                ), 
                                loop
                            )
                        except RuntimeError:
                            asyncio.run(
                                post_fn(
                                    chat_id=msg_meta.telegram_channel_id, 
                                    message_id=msg_meta.telegram_message_id, 
                                    text=text
                                )
                            )
                    else:
                        post_fn(
                            chat_id=msg_meta.telegram_channel_id, 
                            message_id=msg_meta.telegram_message_id, 
                            text=text
                        )
                        
                except Exception as e:
                    logger.warning("فشل إرسال الرد للتوصية #%s إلى القناة %s: %s", 
                                 rec_id, msg_meta.telegram_channel_id, e)

    # ==================== التحقق من صحة البيانات ====================

    def _validate_recommendation_data(self, side: str, entry: float, stop_loss: float, targets: List[Dict[str, float]]):
        """التحقق الشامل من صحة بيانات التوصية"""
        side_upper = side.upper()
        
        # التحقق من الأسعار الإيجابية
        if entry <= 0 or stop_loss <= 0:
            raise ValueError("يجب أن تكون أسعار الدخول ووقف الخسارة موجبة")
            
        # التحقق من وجود أهداف صالحة
        if not targets or not all(t.get('price', 0) > 0 for t in targets):
            raise ValueError("يجب وجود هدف واحد على الأقل بسعر موجب")
            
        # التحقق من علاقة السعر لـ LONG
        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("لصفقات الشراء، يجب أن يكون وقف الخسارة أقل من سعر الدخول")
            
        # التحقق من علاقة السعر لـ SHORT  
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("لصفقات البيع، يجب أن يكون وقف الخسارة أعلى من سعر الدخول")
            
        # حساب المخاطرة والمكافأة
        risk = abs(entry - stop_loss)
        if risk <= 1e-9:
            raise ValueError("لا يمكن أن يكون سعر الدخول ووقف الخسارة متساويين")
            
        # تحديد سعر الهدف الأول
        if side_upper == "LONG":
            first_target_price = min(t['price'] for t in targets)
        else:
            first_target_price = max(t['price'] for t in targets)
            
        reward = abs(first_target_price - entry)
        min_acceptable_rr = 0.1
        
        if (reward / risk) < min_acceptable_rr:
            raise ValueError(f"نسبة المخاطرة/المكافأة منخفضة جداً: {(reward / risk):.3f}. الحد الأدنى المسموح: {min_acceptable_rr}")
        
        # التحقق من تفرد أسعار الأهداف
        target_prices = [t['price'] for t in targets]
        if len(target_prices) != len(set(target_prices)):
            raise ValueError("يجب أن تكون أسعار الأهداف فريدة")
            
        # التحقق من ترتيب الأهداف
        is_long = side_upper == 'LONG'
        sorted_prices = sorted(target_prices, reverse=not is_long)
        if target_prices != sorted_prices:
            raise ValueError("يجب ترتيب الأهداف تصاعدياً لصفقات الشراء وتنازلياً لصفقات البيع")
            
        # التحقق من نسب الإغلاق
        total_close = sum(float(t.get('close_percent', 0)) for t in targets)
        if total_close > 100.01:
            raise ValueError("لا يمكن أن يتجاوز مجموع نسب الإغلاق 100%")

    def _convert_enum_to_string(self, value):
        """تحويل القيم التعدادية إلى نص"""
        if hasattr(value, 'value'):
            return value.value
        return value

    # ==================== إدارة التوصيات ====================

    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, 
                                    user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """نشر التوصية في القنوات المحددة مع تقرير مفصل"""
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(session).find_by_telegram_id(uid_int)
        if not user:
            report["failed"].append({"reason": "المستخدم غير موجود"})
            return rec_entity, report
            
        # الحصول على القنوات المتاحة
        channels_to_publish = ChannelRepository(session).list_by_analyst(user.id, only_active=True)
        if target_channel_ids is not None:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
            
        if not channels_to_publish:
            reason = "لا توجد قنوات نشطة" if target_channel_ids is None else "القنوات المحددة غير نشطة"
            report["failed"].append({"reason": reason})
            return rec_entity, report

        # بناء واجهة البطاقة العامة
        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        
        # النشر في كل قناة
        for channel in channels_to_publish:
            success = False
            last_error = None
            
            for attempt in range(MAX_RETRY_ATTEMPTS):
                try:
                    post_fn = getattr(self.notifier, "post_to_channel", None)
                    if not post_fn:
                        raise RuntimeError("دالة النشر غير متوفرة")
                        
                    result = await self._call_notifier_maybe_async(
                        post_fn, channel.telegram_channel_id, rec_entity, keyboard
                    )
                    
                    if isinstance(result, tuple) and len(result) == 2:
                        publication = PublishedMessage(
                            recommendation_id=rec_entity.id,
                            telegram_channel_id=result[0],
                            telegram_message_id=result[1]
                        )
                        session.add(publication)
                        report["success"].append({
                            "channel_id": channel.telegram_channel_id, 
                            "message_id": result[1]
                        })
                        success = True
                        break
                    else:
                        raise RuntimeError(f"نوع الاستجابة غير مدعوم: {type(result)}")
                        
                except Exception as e:
                    last_error = e
                    logger.warning("محاولة النشر %d فشلت للقناة %s: %s", 
                                 attempt + 1, channel.telegram_channel_id, e)
                    await asyncio.sleep(RETRY_DELAY_BASE * (attempt + 1))
                    
            if not success:
                error_msg = str(last_error) if last_error else "خطأ غير معروف"
                report["failed"].append({
                    "channel_id": channel.telegram_channel_id, 
                    "reason": error_msg
                })

        # حفظ سجلات النشر
        try:
            session.flush()
        except Exception:
            logger.exception("فشل حفظ سجلات الرسائل المنشورة")
            
        return rec_entity, report

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """إنشاء توصية جديدة ونشرها - الإصدار النهائي"""
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(db_session).find_by_telegram_id(uid_int)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("فقط المحللون يمكنهم إنشاء التوصيات")

        # تجهيز البيانات الأساسية
        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        
        # معالجة نوع الطلب
        order_type_input = kwargs['order_type']
        order_type_str = self._convert_enum_to_string(order_type_input)
        order_type_enum = OrderType(order_type_str)
        
        # تحديد الحالة بناءً على نوع الطلب
        status, final_entry = RecommendationStatusEnum.PENDING, kwargs['entry']
        if order_type_enum == OrderType.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            if live_price is None:
                raise RuntimeError(f"لا يمكن الحصول على السعر الحي للأصل {asset}")
            status, final_entry = RecommendationStatusEnum.ACTIVE, live_price
        
        # التحقق من صحة البيانات
        targets_list = kwargs['targets']
        self._validate_recommendation_data(side, final_entry, kwargs['stop_loss'], targets_list)

        # إنشاء التوصية في قاعدة البيانات
        rec_orm = Recommendation(
            analyst_id=user.id,
            asset=asset,
            side=side,
            entry=final_entry,
            stop_loss=kwargs['stop_loss'],
            targets=targets_list,
            order_type=order_type_str,
            status=self._convert_enum_to_string(status),
            market=market,
            notes=kwargs.get('notes'),
            exit_strategy=self._convert_enum_to_string(kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP)),
            open_size_percent=100.0,
            is_shadow=False,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )
        
        db_session.add(rec_orm)
        db_session.flush()

        # تسجيل الحدث
        event_type = "CREATED_ACTIVE" if rec_orm.status == RecommendationStatusEnum.ACTIVE.value else "CREATED_PENDING"
        new_event = RecommendationEvent(
            recommendation_id=rec_orm.id, 
            event_type=event_type, 
            event_data={}
        )
        db_session.add(new_event)
        db_session.flush()
        
        db_session.refresh(rec_orm)

        # تحويل إلى كيان وتحديث التنبيهات
        created_rec_entity = self.repo._to_entity(rec_orm)
        await self.alert_service.update_triggers_for_recommendation(created_rec_entity.id)
        
        # النشر في القنوات
        final_rec, report = await self._publish_recommendation(
            db_session, created_rec_entity, user_id, kwargs.get('target_channel_ids')
        )
        
        return final_rec, report

    def get_recommendation_for_user(self, db_session: Session, rec_id: int, user_telegram_id: str) -> Optional[RecommendationEntity]:
        """الحصول على توصية محددة للمستخدم مع التحقق من الصلاحية"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user:
            return None
        
        if user.user_type == UserType.ANALYST:
            rec_orm = self.repo.get(db_session, rec_id)
            if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
                return None
            return self.repo._to_entity(rec_orm)
        
        return None

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str, **filters) -> List[Any]:
        """الحصول على جميع الصفقات المفتوحة للمستخدم"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user:
            return []
        
        if user.user_type == UserType.ANALYST:
            recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id)
            return [self.repo._to_entity(r) for r in recs_orm]
        else:
            trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id)
            return trades_orm

    def get_recent_assets_for_user(self, db_session: Session, user_telegram_id: str, limit: int = 5) -> List[str]:
        """الحصول على أحدث الأصول التي تداولها المستخدم"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user:
            return []
            
        if user.user_type == UserType.ANALYST:
            recs = self.repo.get_open_recs_for_analyst(db_session, user.id)
            assets = list(set([r.asset for r in recs]))[:limit]
        else:
            trades = self.repo.get_open_trades_for_trader(db_session, user.id)
            assets = list(set([t.asset for t in trades]))[:limit]
            
        return assets if assets else ["BTCUSDT", "ETHUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT"]

    async def cancel_pending_recommendation_manual(self, rec_id: int, user_telegram_id: str, db_session: Session) -> RecommendationEntity:
        """إلغاء توصية معلقة يدوياً"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("فقط المحللون يمكنهم إلغاء التوصيات")
            
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو غير مسموح بالوصول")
            
        if rec_orm.status != RecommendationStatusEnum.PENDING:
            raise ValueError("يمكن إلغاء التوصيات المعلقة فقط")
            
        # تحديث حالة التوصية
        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        # تسجيل الحدث
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="CANCELLED_MANUALLY",
            event_data={"cancelled_by": user_telegram_id}
        )
        db_session.add(event)
        
        # إزالة التنبيهات
        await self.alert_service.remove_triggers_for_item(rec_id, is_user_trade=False)
        
        logger.info("تم إلغاء التوصية المعلقة #%s بواسطة المستخدم %s", rec_id, user_telegram_id)
        return self.repo._to_entity(rec_orm)

    async def close_recommendation_for_user_async(self, rec_id: int, user_telegram_id: str, 
                                                exit_price: float, reason: str = "MANUAL_CLOSE", 
                                                db_session: Session = None) -> RecommendationEntity:
        """إغلاق توصية للمستخدم بسعر محدد"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("فقط المحللون يمكنهم إغلاق التوصيات")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو غير مسموح بالوصول")
            
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            raise ValueError("التوصية مغلقة بالفعل")
            
        # تحديث حالة التوصية
        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.exit_price = exit_price
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        # تسجيل الحدث
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="CLOSED_MANUALLY",
            event_data={
                "exit_price": exit_price,
                "reason": reason,
                "closed_by": user_telegram_id
            }
        )
        db_session.add(event)
        
        # تحديث الواجهة وإزالة التنبيهات
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        await self.alert_service.remove_triggers_for_item(rec_id, is_user_trade=False)
        
        logger.info("تم إغلاق التوصية #%s بسعر %s بواسطة المستخدم %s", rec_id, exit_price, user_telegram_id)
        return rec_entity

    async def close_recommendation_at_market_for_user_async(self, rec_id: int, user_telegram_id: str, db_session: Session) -> RecommendationEntity:
        """إغلاق توصية بسعر السوق الحالي"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("فقط المحللون يمكنهم إغلاق التوصيات")
            
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو غير مسموح بالوصول")
            
        # الحصول على سعر السوق
        market_price = await self.price_service.get_cached_price(rec_orm.asset, rec_orm.market, force_refresh=True)
        if not market_price:
            raise ValueError("لا يمكن الحصول على سعر السوق الحالي")
            
        return await self.close_recommendation_for_user_async(
            rec_id, user_telegram_id, market_price, "MARKET_CLOSE", db_session=db_session
        )

    async def update_sl_for_user_async(self, rec_id: int, user_telegram_id: str, new_sl: float, db_session: Session) -> RecommendationEntity:
        """تحديث وقف الخسارة لتوصية"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("فقط المحللون يمكنهم تحديث التوصيات")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو غير مسموح بالوصول")
            
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("يمكن تحديث التوصيات النشطة فقط")
            
        # التحقق من صحة وقف الخسارة الجديد
        self._validate_recommendation_data(
            rec_orm.side, float(rec_orm.entry), new_sl, rec_orm.targets
        )
            
        old_sl = rec_orm.stop_loss
        rec_orm.stop_loss = new_sl
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        # تسجيل الحدث
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="SL_UPDATED",
            event_data={
                "old_sl": float(old_sl),
                "new_sl": new_sl,
                "updated_by": user_telegram_id
            }
        )
        db_session.add(event)
        
        # تحديث الواجهة والتنبيهات
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        
        logger.info("تم تحديث وقف الخسارة للتوصية #%s من %s إلى %s بواسطة %s", 
                  rec_id, old_sl, new_sl, user_telegram_id)
        return rec_entity

    async def update_targets_for_user_async(self, rec_id: int, user_telegram_id: str, new_targets: List[Dict[str, float]], db_session: Session) -> RecommendationEntity:
        """تحديث الأهداف لتوصية"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("فقط المحللون يمكنهم تحديث التوصيات")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو غير مسموح بالوصول")
            
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("يمكن تحديث التوصيات النشطة فقط")
            
        # التحقق من صحة الأهداف الجديدة
        self._validate_recommendation_data(
            rec_orm.side, float(rec_orm.entry), float(rec_orm.stop_loss), new_targets
        )
        
        old_targets = rec_orm.targets
        rec_orm.targets = new_targets
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        # تسجيل الحدث
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="TARGETS_UPDATED",
            event_data={
                "old_targets": old_targets,
                "new_targets": new_targets,
                "updated_by": user_telegram_id
            }
        )
        db_session.add(event)
        
        # تحديث الواجهة والتنبيهات
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        
        logger.info("تم تحديث الأهداف للتوصية #%s بواسطة المستخدم %s", rec_id, user_telegram_id)
        return rec_entity

    # ==================== معالجة الأحداث التلقائية ====================

    async def process_activation_event(self, rec_id: int):
        """معالجة حدث تفعيل التوصية تلقائياً"""
        with session_scope() as session:
            rec_orm = self.repo.get_for_update(session, rec_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.PENDING:
                return
                
            rec_orm.status = RecommendationStatusEnum.ACTIVE
            rec_orm.activated_at = datetime.now(timezone.utc)
            rec_orm.updated_at = datetime.now(timezone.utc)
            
            event = RecommendationEvent(
                recommendation_id=rec_id,
                event_type="ACTIVATED_AUTO",
                event_data={"activated_at": rec_orm.activated_at.isoformat()}
            )
            session.add(event)
            
            rec_entity = self.repo._to_entity(rec_orm)
            await self.notify_card_update(rec_entity)
            
            logger.info("تم التفعيل التلقائي للتوصية #%s", rec_id)

    async def process_invalidation_event(self, rec_id: int):
        """معالجة حدث إلغاء التوصية تلقائياً"""
        with session_scope() as session:
            rec_orm = self.repo.get_for_update(session, rec_id)
            if not rec_orm or rec_orm.status != RecommendationStatusEnum.PENDING:
                return
                
            rec_orm.status = RecommendationStatusEnum.CLOSED
            rec_orm.closed_at = datetime.now(timezone.utc)
            rec_orm.updated_at = datetime.now(timezone.utc)
            
            event = RecommendationEvent(
                recommendation_id=rec_id,
                event_type="INVALIDATED_AUTO",
                event_data={"invalidated_at": rec_orm.closed_at.isoformat()}
            )
            session.add(event)
            
            rec_entity = self.repo._to_entity(rec_orm)
            await self.notify_card_update(rec_entity)
            
            logger.info("تم الإلغاء التلقائي للتوصية #%s (وصول وقف الخسارة قبل الدخول)", rec_id)

    # ==================== منطق توصية الظل ====================

    async def track_forwarded_trade(self, user_id: str, trade_data: Dict[str, Any], db_session: Session) -> Dict[str, Any]:
        """تتبع صفقة معاد توجيهها عن طريق إنشاء توصية ظل"""
        try:
            trader_user = self._get_user_by_telegram_id(db_session, user_id)
            if not trader_user:
                return {'success': False, 'error': 'المستخدم غير موجود'}

            system_user = self._get_or_create_system_user(db_session)

            # إنشاء توصية ظل
            shadow_rec = Recommendation(
                analyst_id=system_user.id,
                asset=trade_data['asset'],
                side=trade_data['side'],
                entry=float(trade_data['entry']),
                stop_loss=float(trade_data['stop_loss']),
                targets=trade_data['targets'],
                status=RecommendationStatusEnum.ACTIVE,
                order_type=OrderTypeEnum.MARKET,
                notes="صفقة معاد توجيهها من المستخدم",
                market="Futures",
                is_shadow=True,
                activated_at=datetime.now(timezone.utc)
            )
            db_session.add(shadow_rec)
            db_session.flush()
            
            # إنشاء صفقة المستخدم المرتبطة
            new_trade = UserTrade(
                user_id=trader_user.id,
                source_recommendation_id=shadow_rec.id,
                asset=trade_data['asset'],
                side=trade_data['side'],
                entry=float(trade_data['entry']),
                stop_loss=float(trade_data['stop_loss']),
                targets=trade_data['targets'],
                status=UserTradeStatus.OPEN,
                source_forwarded_text=str(trade_data)
            )
            
            db_session.add(new_trade)
            db_session.flush()
            
            # تحديث فهارس التنبيهات
            await self.alert_service.build_triggers_index()
            
            logger.info("تمت إضافة الصفقة المعاد توجيهها #%s للمستخدم %s عبر توصية الظل #%s", 
                      new_trade.id, user_id, shadow_rec.id)
            
            return {
                'success': True,
                'trade_id': new_trade.id,
                'asset': new_trade.asset,
                'side': new_trade.side,
                'status': 'ADDED'
            }
            
        except Exception as e:
            logger.error("فشل تتبع الصفقة المعاد توجيهها للمستخدم %s: %s", user_id, e, exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    async def close_user_trade_async(self, trade_id: int, user_telegram_id: str, exit_price: float, db_session: Session) -> Dict[str, Any]:
        """إغلاق صفقة مستخدم وإغلاق توصية الظل المرتبطة بها"""
        try:
            user = self._get_user_by_telegram_id(db_session, user_telegram_id)
            if not user:
                return {'success': False, 'error': 'المستخدم غير موجود'}
                
            trade = db_session.query(UserTrade).filter(
                UserTrade.id == trade_id,
                UserTrade.user_id == user.id
            ).first()
            
            if not trade:
                return {'success': False, 'error': 'الصفقة غير موجودة أو غير مسموح بالوصول'}
                
            if trade.status == UserTradeStatus.CLOSED:
                return {'success': False, 'error': 'الصفقة مغلقة بالفعل'}
                
            # تحديث حالة الصفقة
            trade.status = UserTradeStatus.CLOSED
            trade.close_price = exit_price
            trade.closed_at = datetime.now(timezone.utc)
            
            # حساب الربح/الخسارة
            if trade.side.upper() == "LONG":
                pnl_pct = ((exit_price - float(trade.entry)) / float(trade.entry)) * 100
            else:
                pnl_pct = ((float(trade.entry) - exit_price) / float(trade.entry)) * 100
                
            trade.pnl_percentage = pnl_pct
            
            # إغلاق توصية الظل المرتبطة إذا كانت موجودة
            if trade.source_recommendation_id:
                shadow_rec = self.repo.get(db_session, trade.source_recommendation_id)
                if shadow_rec and shadow_rec.status == RecommendationStatusEnum.ACTIVE:
                    shadow_rec.status = RecommendationStatusEnum.CLOSED
                    shadow_rec.exit_price = exit_price
                    shadow_rec.closed_at = datetime.now(timezone.utc)
                    shadow_rec.updated_at = datetime.now(timezone.utc)
                    
                    shadow_event = RecommendationEvent(
                        recommendation_id=shadow_rec.id,
                        event_type="SHADOW_CLOSED",
                        event_data={
                            "user_trade_id": trade_id,
                            "exit_price": exit_price,
                            "closed_by": user_telegram_id
                        }
                    )
                    db_session.add(shadow_event)
                    
                    logger.info("تم إغلاق توصية الظل #%s المرتبطة بالصفقة #%s", shadow_rec.id, trade_id)
            
            # تحديث فهارس التنبيهات
            await self.alert_service.build_triggers_index()
            
            logger.info("تم إغلاق صفقة المستخدم #%s بسعر %s للمستخدم %s", trade_id, exit_price, user_telegram_id)
            
            return {
                'success': True,
                'trade_id': trade_id,
                'asset': trade.asset,
                'side': trade.side,
                'pnl_percent': pnl_pct,
                'status': 'CLOSED'
            }
            
        except Exception as e:
            logger.error("فشل إغلاق صفقة المستخدم #%s: %s", trade_id, e, exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    async def update_user_trade_sl_async(self, trade_id: int, user_telegram_id: str, new_sl: float, db_session: Session) -> Dict[str, Any]:
        """تحديث وقف الخسارة لصفقة مستخدم"""
        try:
            user = self._get_user_by_telegram_id(db_session, user_telegram_id)
            if not user:
                return {'success': False, 'error': 'المستخدم غير موجود'}
                
            trade = db_session.query(UserTrade).filter(
                UserTrade.id == trade_id,
                UserTrade.user_id == user.id
            ).first()
            
            if not trade:
                return {'success': False, 'error': 'الصفقة غير موجودة أو غير مسموح بالوصول'}
                
            if trade.status != UserTradeStatus.OPEN:
                return {'success': False, 'error': 'يمكن تحديث الصفقات المفتوحة فقط'}
            
            # التحقق من صحة وقف الخسارة الجديد
            self._validate_recommendation_data(
                trade.side, float(trade.entry), new_sl, trade.targets
            )
            
            old_sl = trade.stop_loss
            trade.stop_loss = new_sl
            
            # تحديث توصية الظل المرتبطة إذا كانت موجودة
            if trade.source_recommendation_id:
                shadow_rec = self.repo.get(db_session, trade.source_recommendation_id)
                if shadow_rec and shadow_rec.status == RecommendationStatusEnum.ACTIVE:
                    shadow_rec.stop_loss = new_sl
                    shadow_rec.updated_at = datetime.now(timezone.utc)
                    
                    shadow_event = RecommendationEvent(
                        recommendation_id=shadow_rec.id,
                        event_type="SHADOW_SL_UPDATED",
                        event_data={
                            "user_trade_id": trade_id,
                            "old_sl": float(old_sl),
                            "new_sl": new_sl,
                            "updated_by": user_telegram_id
                        }
                    )
                    db_session.add(shadow_event)
            
            # تحديث التنبيهات
            await self.alert_service.build_triggers_index()
            
            logger.info("تم تحديث وقف الخسارة للصفقة #%s من %s إلى %s بواسطة %s", 
                      trade_id, old_sl, new_sl, user_telegram_id)
            
            return {
                'success': True,
                'trade_id': trade_id,
                'asset': trade.asset,
                'old_sl': float(old_sl),
                'new_sl': new_sl,
                'status': 'UPDATED'
            }
            
        except Exception as e:
            logger.error("فشل تحديث وقف الخسارة للصفقة #%s: %s", trade_id, e, exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    async def update_user_trade_targets_async(self, trade_id: int, user_telegram_id: str, new_targets: List[Dict[str, float]], db_session: Session) -> Dict[str, Any]:
        """تحديث أهداف صفقة مستخدم"""
        try:
            user = self._get_user_by_telegram_id(db_session, user_telegram_id)
            if not user:
                return {'success': False, 'error': 'المستخدم غير موجود'}
                
            trade = db_session.query(UserTrade).filter(
                UserTrade.id == trade_id,
                UserTrade.user_id == user.id
            ).first()
            
            if not trade:
                return {'success': False, 'error': 'الصفقة غير موجودة أو غير مسموح بالوصول'}
                
            if trade.status != UserTradeStatus.OPEN:
                return {'success': False, 'error': 'يمكن تحديث الصفقات المفتوحة فقط'}
            
            # التحقق من صحة الأهداف الجديدة
            self._validate_recommendation_data(
                trade.side, float(trade.entry), float(trade.stop_loss), new_targets
            )
            
            old_targets = trade.targets
            trade.targets = new_targets
            
            # تحديث توصية الظل المرتبطة إذا كانت موجودة
            if trade.source_recommendation_id:
                shadow_rec = self.repo.get(db_session, trade.source_recommendation_id)
                if shadow_rec and shadow_rec.status == RecommendationStatusEnum.ACTIVE:
                    shadow_rec.targets = new_targets
                    shadow_rec.updated_at = datetime.now(timezone.utc)
                    
                    shadow_event = RecommendationEvent(
                        recommendation_id=shadow_rec.id,
                        event_type="SHADOW_TARGETS_UPDATED",
                        event_data={
                            "user_trade_id": trade_id,
                            "old_targets": old_targets,
                            "new_targets": new_targets,
                            "updated_by": user_telegram_id
                        }
                    )
                    db_session.add(shadow_event)
            
            # تحديث التنبيهات
            await self.alert_service.build_triggers_index()
            
            logger.info("تم تحديث أهداف الصفقة #%s بواسطة المستخدم %s", trade_id, user_telegram_id)
            
            return {
                'success': True,
                'trade_id': trade_id,
                'asset': trade.asset,
                'targets_count': len(new_targets),
                'status': 'UPDATED'
            }
            
        except Exception as e:
            logger.error("فشل تحديث أهداف الصفقة #%s: %s", trade_id, e, exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def get_user_trade_details(self, db_session: Session, trade_id: int, user_telegram_id: str) -> Optional[Dict[str, Any]]:
        """الحصول على تفاصيل صفقة مستخدم محددة"""
        try:
            user = self._get_user_by_telegram_id(db_session, user_telegram_id)
            if not user:
                return None
                
            trade = db_session.query(UserTrade).filter(
                UserTrade.id == trade_id,
                UserTrade.user_id == user.id
            ).first()
            
            if not trade:
                return None
            
            # حساب الربح/الخسارة الحالي إذا كانت الصفقة مفتوحة
            current_pnl = None
            if trade.status == UserTradeStatus.OPEN:
                try:
                    current_price = asyncio.run(self.price_service.get_cached_price(trade.asset, "Futures"))
                    if current_price:
                        if trade.side.upper() == "LONG":
                            current_pnl = ((current_price - float(trade.entry)) / float(trade.entry)) * 100
                        else:
                            current_pnl = ((float(trade.entry) - current_price) / float(trade.entry)) * 100
                except Exception:
                    pass
            
            return {
                'id': trade.id,
                'asset': trade.asset,
                'side': trade.side,
                'entry': float(trade.entry),
                'stop_loss': float(trade.stop_loss),
                'targets': trade.targets,
                'status': trade.status.value,
                'current_pnl': current_pnl,
                'realized_pnl': float(trade.pnl_percentage) if trade.pnl_percentage else None,
                'close_price': float(trade.close_price) if trade.close_price else None,
                'created_at': trade.created_at.isoformat(),
                'closed_at': trade.closed_at.isoformat() if trade.closed_at else None,
                'source_recommendation_id': trade.source_recommendation_id,
                'is_shadow_trade': trade.source_recommendation_id is not None
            }
            
        except Exception as e:
            logger.error("فشل الحصول على تفاصيل الصفقة #%s: %s", trade_id, e, exc_info=True)
            return None

    async def get_user_portfolio_summary(self, db_session: Session, user_telegram_id: str) -> Dict[str, Any]:
        """الحصول على ملخص محفظة المستخدم"""
        try:
            user = self._get_user_by_telegram_id(db_session, user_telegram_id)
            if not user:
                return {'error': 'المستخدم غير موجود'}
            
            # الصفقات المفتوحة
            open_trades = self.repo.get_open_trades_for_trader(db_session, user.id)
            
            # الصفقات المغلقة
            closed_trades = db_session.query(UserTrade).filter(
                UserTrade.user_id == user.id,
                UserTrade.status == UserTradeStatus.CLOSED
            ).all()
            
            # حساب الإحصائيات
            total_trades = len(open_trades) + len(closed_trades)
            winning_trades = [t for t in closed_trades if t.pnl_percentage and float(t.pnl_percentage) > 0]
            losing_trades = [t for t in closed_trades if t.pnl_percentage and float(t.pnl_percentage) <= 0]
            
            win_rate = (len(winning_trades) / len(closed_trades)) * 100 if closed_trades else 0
            total_pnl = sum(float(t.pnl_percentage) for t in closed_trades if t.pnl_percentage) if closed_trades else 0
            avg_win = sum(float(t.pnl_percentage) for t in winning_trades) / len(winning_trades) if winning_trades else 0
            avg_loss = sum(float(t.pnl_percentage) for t in losing_trades) / len(losing_trades) if losing_trades else 0
            
            # الأصول الأكثر تداولاً
            asset_counts = {}
            all_trades = open_trades + closed_trades
            for trade in all_trades:
                asset_counts[trade.asset] = asset_counts.get(trade.asset, 0) + 1
            
            top_assets = sorted(asset_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            
            return {
                'success': True,
                'user_id': user_telegram_id,
                'portfolio_summary': {
                    'total_trades': total_trades,
                    'open_trades': len(open_trades),
                    'closed_trades': len(closed_trades),
                    'winning_trades': len(winning_trades),
                    'losing_trades': len(losing_trades),
                    'win_rate': round(win_rate, 2),
                    'total_pnl': round(total_pnl, 2),
                    'avg_win': round(avg_win, 2),
                    'avg_loss': round(avg_loss, 2),
                    'top_assets': [asset for asset, count in top_assets]
                },
                'open_positions': [
                    {
                        'id': trade.id,
                        'asset': trade.asset,
                        'side': trade.side,
                        'entry': float(trade.entry),
                        'current_pnl': await self._calculate_current_pnl(trade)
                    }
                    for trade in open_trades
                ]
            }
            
        except Exception as e:
            logger.error("فشل الحصول على ملخص محفظة المستخدم %s: %s", user_telegram_id, e, exc_info=True)
            return {'success': False, 'error': str(e)}

    async def _calculate_current_pnl(self, trade: UserTrade) -> Optional[float]:
        """حساب الربح/الخسارة الحالي للصفقة"""
        try:
            current_price = await self.price_service.get_cached_price(trade.asset, "Futures")
            if not current_price:
                return None
                
            if trade.side.upper() == "LONG":
                return ((current_price - float(trade.entry)) / float(trade.entry)) * 100
            else:
                return ((float(trade.entry) - current_price) / float(trade.entry)) * 100
                
        except Exception:
            return None

    # ==================== دوال التقرير والإحصائيات ====================

    async def generate_analyst_performance_report(self, db_session: Session, analyst_telegram_id: str, days: int = 30) -> Dict[str, Any]:
        """إنشاء تقرير أداء للمحلل"""
        try:
            user = self._get_user_by_telegram_id(db_session, analyst_telegram_id)
            if not user or not self._check_analyst_permission(user):
                return {'success': False, 'error': 'المستخدم غير موجود أو ليس محللاً'}
            
            # حساب الفترة الزمنية
            from_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            # التوصيات في الفترة المحددة
            recommendations = db_session.query(Recommendation).filter(
                Recommendation.analyst_id == user.id,
                Recommendation.created_at >= from_date
            ).all()
            
            # الإحصائيات
            total_recommendations = len(recommendations)
            active_recommendations = len([r for r in recommendations if r.status == RecommendationStatusEnum.ACTIVE])
            closed_recommendations = len([r for r in recommendations if r.status == RecommendationStatusEnum.CLOSED])
            pending_recommendations = len([r for r in recommendations if r.status == RecommendationStatusEnum.PENDING])
            
            # التوصيات المغلقة فقط للأداء
            closed_recs = [r for r in recommendations if r.status == RecommendationStatusEnum.CLOSED and r.exit_price]
            winning_recs = [r for r in closed_recs if self._is_winning_recommendation(r)]
            losing_recs = [r for r in closed_recs if not self._is_winning_recommendation(r)]
            
            win_rate = (len(winning_recs) / len(closed_recs)) * 100 if closed_recs else 0
            avg_pnl = sum(self._calculate_recommendation_pnl(r) for r in closed_recs) / len(closed_recs) if closed_recs else 0
            
            return {
                'success': True,
                'analyst_id': analyst_telegram_id,
                'period_days': days,
                'performance_summary': {
                    'total_recommendations': total_recommendations,
                    'active_recommendations': active_recommendations,
                    'closed_recommendations': closed_recommendations,
                    'pending_recommendations': pending_recommendations,
                    'winning_recommendations': len(winning_recs),
                    'losing_recommendations': len(losing_recs),
                    'win_rate': round(win_rate, 2),
                    'average_pnl': round(avg_pnl, 2),
                    'total_pnl': round(sum(self._calculate_recommendation_pnl(r) for r in closed_recs), 2) if closed_recs else 0
                },
                'top_performing_assets': self._get_top_performing_assets(closed_recs),
                'recent_activity': [
                    {
                        'id': r.id,
                        'asset': r.asset,
                        'side': r.side,
                        'status': r.status.value,
                        'created_at': r.created_at.isoformat(),
                        'pnl': self._calculate_recommendation_pnl(r) if r.exit_price else None
                    }
                    for r in recommendations[:10]  # آخر 10 توصيات
                ]
            }
            
        except Exception as e:
            logger.error("فشل إنشاء تقرير أداء المحلل %s: %s", analyst_telegram_id, e, exc_info=True)
            return {'success': False, 'error': str(e)}

    def _is_winning_recommendation(self, recommendation: Recommendation) -> bool:
        """تحديد إذا كانت التوصية رابحة"""
        if not recommendation.exit_price:
            return False
            
        entry = float(recommendation.entry)
        exit_price = float(recommendation.exit_price)
        
        if recommendation.side.upper() == "LONG":
            return exit_price > entry
        else:
            return exit_price < entry

    def _calculate_recommendation_pnl(self, recommendation: Recommendation) -> float:
        """حساب الربح/الخسارة للتوصية"""
        if not recommendation.exit_price:
            return 0.0
            
        entry = float(recommendation.entry)
        exit_price = float(recommendation.exit_price)
        
        if recommendation.side.upper() == "LONG":
            return ((exit_price - entry) / entry) * 100
        else:
            return ((entry - exit_price) / entry) * 100

    def _get_top_performing_assets(self, recommendations: List[Recommendation]) -> List[Dict[str, Any]]:
        """الحصول على أفضل الأصول أداءً"""
        asset_performance = {}
        
        for rec in recommendations:
            if rec.asset not in asset_performance:
                asset_performance[rec.asset] = {
                    'total_trades': 0,
                    'winning_trades': 0,
                    'total_pnl': 0.0
                }
            
            asset_performance[rec.asset]['total_trades'] += 1
            pnl = self._calculate_recommendation_pnl(rec)
            asset_performance[rec.asset]['total_pnl'] += pnl
            
            if self._is_winning_recommendation(rec):
                asset_performance[rec.asset]['winning_trades'] += 1
        
        # حساب متوسط الأداء لكل أصل
        for asset in asset_performance:
            stats = asset_performance[asset]
            stats['win_rate'] = (stats['winning_trades'] / stats['total_trades']) * 100 if stats['total_trades'] > 0 else 0
            stats['avg_pnl'] = stats['total_pnl'] / stats['total_trades'] if stats['total_trades'] > 0 else 0
        
        # ترتيب حسب متوسط الربح
        sorted_assets = sorted(
            asset_performance.items(),
            key=lambda x: x[1]['avg_pnl'],
            reverse=True
        )[:5]  # أفضل 5 أصول
        
        return [
            {
                'asset': asset,
                'total_trades': stats['total_trades'],
                'win_rate': round(stats['win_rate'], 2),
                'avg_pnl': round(stats['avg_pnl'], 2)
            }
            for asset, stats in sorted_assets
        ]


# تصدير الفئة والثوابت
__all__ = ['TradeService', 'SYSTEM_USER_ID_FOR_FORWARDING']