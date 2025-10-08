# src/capitalguard/application/services/trade_service.py (v24.0 - FINAL PRODUCTION READY)
"""
TradeService - الإصدار النهائي الكامل والداعم لتعدد المستخدمين مع منطق "توصية الظل"
وجميع وظائف معالجة الأحداث للمحللين والمتداولين.
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
RETRY_DELAY_BASE = 0.2

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
        """تحديث بطاقة التوصية في القنوات - لا يتم تحديث توصيات الظل"""
        if getattr(rec_entity, 'is_shadow', False):
            return  # لا يتم تحديث البطاقات لتوصيات الظل

        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec_entity.id)
            if not published_messages:
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
                    logger.error("فشل تحديث البطاقة للتوصية %s في القناة %s: %s", 
                               rec_entity.id, msg_meta.telegram_channel_id, e)

    def notify_reply(self, rec_id: int, text: str):
        """إرسال رد على التوصية في القنوات - لا يتم إرسال ردود لتوصيات الظل"""
        with session_scope() as session:
            rec = self.repo.get(session, rec_id)
            if not rec or getattr(rec, 'is_shadow', False):
                return  # لا يتم إرسال ردود لتوصيات الظل

            published_messages = self.repo.get_published_messages(session, rec_id)
            for msg_meta in published_messages:
                try:
                    post_fn = getattr(self.notifier, "post_notification_reply", None)
                    if not post_fn:
                        logger.error("الدالة post_notification_reply غير متوفرة في الإشعار")
                        continue
                    
                    # إرسال غير متزامن للردود
                    asyncio.create_task(
                        self._call_notifier_maybe_async(
                            post_fn, 
                            chat_id=msg_meta.telegram_channel_id, 
                            message_id=msg_meta.telegram_message_id, 
                            text=text
                        )
                    )
                except Exception as e:
                    logger.warning("فشل إرسال الرد للتوصية #%s إلى القناة %s: %s", 
                                 rec_id, msg_meta.telegram_channel_id, e)

    # ==================== التحقق من صحة البيانات ====================

    def _validate_recommendation_data(self, side: str, entry: float, stop_loss: float, targets: List[Dict[str, float]]):
        """التحقق الشامل من صحة بيانات التوصية"""
        side_upper = side.upper()
        
        if not all(isinstance(p, (int, float)) and p > 0 for p in [entry, stop_loss]):
            raise ValueError("يجب أن تكون أسعار الدخول ووقف الخسارة أرقاماً موجبة")
            
        if not targets or not all(isinstance(t.get('price'), (int, float)) and t.get('price', 0) > 0 for t in targets):
            raise ValueError("يجب وجود هدف واحد على الأقل بسعر موجب")
            
        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("لصفقات الشراء، يجب أن يكون وقف الخسارة أقل من سعر الدخول")
            
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("لصفقات البيع، يجب أن يكون وقف الخسارة أعلى من سعر الدخول")
        
        target_prices = [t['price'] for t in targets]
        if side_upper == 'LONG' and any(p <= entry for p in target_prices):
            raise ValueError("يجب أن تكون جميع أسعار الأهداف أعلى من سعر الدخول لصفقات الشراء")
            
        if side_upper == 'SHORT' and any(p >= entry for p in target_prices):
            raise ValueError("يجب أن تكون جميع أسعار الأهداف أقل من سعر الدخول لصفقات البيع")

    def _convert_enum_to_string(self, value):
        """تحويل القيم التعدادية إلى نص"""
        if hasattr(value, 'value'):
            return value.value
        return value

    # ==================== إدارة التوصيات ====================

    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, 
                                    user_id: str, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """نشر التوصية في القنوات المحددة"""
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(session).find_by_telegram_id(uid_int)
        if not user:
            report["failed"].append({"reason": "المستخدم غير موجود"})
            return rec_entity, report
            
        channels_to_publish = ChannelRepository(session).list_by_analyst(user.id, only_active=True)
        if target_channel_ids is not None:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
            
        if not channels_to_publish:
            reason = "لا توجد قنوات نشطة مرتبطة"
            report["failed"].append({"reason": reason})
            return rec_entity, report

        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        
        for channel in channels_to_publish:
            try:
                post_fn = getattr(self.notifier, "post_to_channel", None)
                if not post_fn:
                    raise RuntimeError("دالة النشر غير متوفرة في الإشعار")
                    
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
                else:
                    raise RuntimeError(f"نوع الاستجابة غير مدعوم: {type(result)}")
                    
            except Exception as e:
                report["failed"].append({
                    "channel_id": channel.telegram_channel_id, 
                    "reason": str(e)
                })

        session.flush()
        return rec_entity, report

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """إنشاء توصية جديدة ونشرها"""
        uid_int = _parse_int_user_id(user_id)
        user = UserRepository(db_session).find_by_telegram_id(uid_int)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("فقط المحللون يمكنهم إنشاء التوصيات")

        # تجهيز البيانات الأساسية
        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        
        # معالجة نوع الطلب
        order_type_enum = OrderType(self._convert_enum_to_string(kwargs['order_type']))
        
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
            order_type=order_type_enum,
            status=status,
            market=market,
            notes=kwargs.get('notes'),
            exit_strategy=ExitStrategyEnum[self._convert_enum_to_string(kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP))],
            is_shadow=False,
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )
        
        db_session.add(rec_orm)
        db_session.flush()

        # تسجيل الحدث
        event_type = "CREATED_ACTIVE" if rec_orm.status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING"
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
        await self.alert_service.update_triggers_for_item(created_rec_entity.id, is_user_trade=False)
        
        # النشر في القنوات
        final_rec, report = await self._publish_recommendation(
            db_session, created_rec_entity, user_id, kwargs.get('target_channel_ids')
        )
        
        return final_rec, report

    def get_open_positions_for_user(self, db_session: Session, user_telegram_id: str, **filters) -> List[Any]:
        """الحصول على جميع الصفقات المفتوحة للمستخدم"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user: 
            return []
        
        open_positions = []
        
        # المحللون يرون توصياتهم الرسمية + صفقاتهم الشخصية
        if user.user_type == UserType.ANALYST:
            recs_orm = self.repo.get_open_recs_for_analyst(db_session, user.id)
            for rec in recs_orm:
                rec_entity = self.repo._to_entity(rec)
                if rec_entity:
                    setattr(rec_entity, 'is_user_trade', False)
                    open_positions.append(rec_entity)

        # جميع المستخدمين (بما في ذلك المحللون) يمكن أن يكون لديهم صفقات شخصية
        trades_orm = self.repo.get_open_trades_for_trader(db_session, user.id)
        for trade in trades_orm:
            trade_entity = RecommendationEntity(
                id=trade.id,
                asset=Symbol(trade.asset),
                side=Side(trade.side),
                entry=Price(float(trade.entry)),
                stop_loss=Price(float(trade.stop_loss)),
                targets=Targets(trade.targets),
                status=RecommendationStatusEntity.ACTIVE,
                order_type=OrderType.MARKET,
                user_id=str(user.telegram_user_id),
                created_at=trade.created_at
            )
            setattr(trade_entity, 'is_user_trade', True)
            open_positions.append(trade_entity)
            
        # ترتيب جميع الصفقات حسب تاريخ الإنشاء
        open_positions.sort(key=lambda p: p.created_at, reverse=True)
        return open_positions

    def get_position_details_for_user(self, db_session: Session, user_telegram_id: str, position_type: str, position_id: int) -> Optional[RecommendationEntity]:
        """
        الحصول على تفاصيل صفقة محددة (سواء كانت توصية أو صفقة شخصية)
        وإرجاعها ككيان RecommendationEntity موحد.
        """
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user:
            return None

        if position_type == 'rec':
            # إنها توصية محلل
            if not self._check_analyst_permission(user): 
                return None  # المتداولون لا يمكنهم إدارة التوصيات الرسمية
                
            rec_orm = self.repo.get(db_session, position_id)
            if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
                return None
                
            rec_entity = self.repo._to_entity(rec_orm)
            if rec_entity:
                setattr(rec_entity, 'is_user_trade', False)
            return rec_entity
        
        elif position_type == 'trade':
            # إنها صفقة شخصية للمستخدم
            trade_orm = self.repo.get_user_trade_by_id(db_session, position_id)
            if not trade_orm or not self._check_trade_ownership(trade_orm, user.id):
                return None
            
            trade_entity = RecommendationEntity(
                id=trade_orm.id,
                asset=Symbol(trade_orm.asset),
                side=Side(trade_orm.side),
                entry=Price(float(trade_orm.entry)),
                stop_loss=Price(float(trade_orm.stop_loss)),
                targets=Targets(trade_orm.targets),
                status=RecommendationStatusEntity.ACTIVE if trade_orm.status == UserTradeStatus.OPEN else RecommendationStatusEntity.CLOSED,
                order_type=OrderType.MARKET,
                user_id=str(user.telegram_user_id),
                created_at=trade_orm.created_at,
                closed_at=trade_orm.closed_at,
                exit_price=float(trade_orm.close_price) if trade_orm.close_price else None
            )
            setattr(trade_entity, 'is_user_trade', True)
            return trade_entity
            
        return None

    def get_recommendation_for_user(self, db_session: Session, rec_id: int, user_telegram_id: str) -> Optional[RecommendationEntity]:
        """الحصول على توصية محددة للمستخدم (للمحللين فقط)"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user:
            return None
        
        if user.user_type == UserType.ANALYST:
            rec_orm = self.repo.get(db_session, rec_id)
            if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
                return None
            return self.repo._to_entity(rec_orm)
        
        return None

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

    # ==================== إدارة التوصيات - المحللين فقط ====================

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
        await self.alert_service.update_triggers_for_item(rec_id, is_user_trade=False)
        
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
        await self.alert_service.update_triggers_for_item(rec_id, is_user_trade=False)
        
        logger.info("تم تحديث الأهداف للتوصية #%s بواسطة المستخدم %s", rec_id, user_telegram_id)
        return rec_entity

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

    # ==================== معالجة أحداث التداول ====================

    async def process_user_trade_tp_hit_event(self, trade_id: int, user_id: str, target_index: int, price: float):
        """معالجة حدث وصول الهدف لصفقة مستخدم"""
        with session_scope() as session:
            logger.info("معالجة وصول الهدف TP%s للصفقة #%s بسعر %s", target_index, trade_id, price)
            
            user = self._get_user_by_telegram_id(session, user_id)
            if not user:
                logger.error("المستخدم غير موجود للمستخدم %s أثناء معالجة حدث الهدف", user_id)
                return

            trade = self.repo.get_user_trade_by_id(session, trade_id)
            if not trade or not self._check_trade_ownership(trade, user.id) or trade.status != UserTradeStatus.OPEN:
                logger.warning("الصفقة #%s غير موجودة أو لا يملكها المستخدم أو غير مفتوحة. تخطي حدث الهدف", trade_id)
                return

            # التحقق إذا كان الهدف النهائي
            is_final_target = (target_index == len(trade.targets))
            if is_final_target:
                await self.close_user_trade_async(trade_id, user_id, price, db_session=session)
                notification_text = f"✅ صفقتك المتابعة للأصل #{trade.asset} وصلت للهدف النهائي وتم إغلاقها!"
            else:
                notification_text = f"🎯 صفقتك المتابعة للأصل #{trade.asset} وصلت الهدف TP{target_index} عند {price}!"

            # إرسال إشعار للمستخدم
            await self._call_notifier_maybe_async(
                self.notifier.send_private_text, 
                chat_id=int(user_id), 
                text=notification_text
            )

    async def process_user_trade_sl_hit_event(self, trade_id: int, user_id: str, price: float):
        """معالجة حدث وصول وقف الخسارة لصفقة مستخدم"""
        with session_scope() as session:
            logger.info("معالجة وصول وقف الخسارة للصفقة #%s بسعر %s", trade_id, price)
            
            user = self._get_user_by_telegram_id(session, user_id)
            if not user:
                logger.error("المستخدم غير موجود للمستخدم %s أثناء معالجة حدث وقف الخسارة", user_id)
                return

            trade = self.repo.get_user_trade_by_id(session, trade_id)
            if not trade or not self._check_trade_ownership(trade, user.id) or trade.status != UserTradeStatus.OPEN:
                logger.warning("الصفقة #%s غير موجودة أو لا يملكها المستخدم أو غير مفتوحة. تخطي حدث وقف الخسارة", trade_id)
                return

            await self.close_user_trade_async(trade_id, user_id, price, db_session=session)
            notification_text = f"🛑 صفقتك المتابعة للأصل #{trade.asset} وصلت وقف الخسارة وتم إغلاقها."
            
            await self._call_notifier_maybe_async(
                self.notifier.send_private_text, 
                chat_id=int(user_id), 
                text=notification_text
            )

    # ==================== دوال إضافية للواجهة ====================

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


# تصدير الفئة والثوابت
__all__ = ['TradeService', 'SYSTEM_USER_ID_FOR_FORWARDING']