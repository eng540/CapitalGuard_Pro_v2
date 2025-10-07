# src/capitalguard/application/services/trade_service.py (v24.0 - FINAL COMPLETE with Shadow Fix)
"""
TradeService - الإصدار النهائي الكامل مع إصلاح حقل Shadow
"""

import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.models import (
    PublishedMessage, Recommendation, RecommendationEvent, User, UserType,
    RecommendationStatusEnum, UserTrade, UserTradeStatus
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

log = logging.getLogger(__name__)

# معرف مستخدم نظامي محجوز لمالك توصيات الظل المعاد توجيهها
SYSTEM_USER_ID_FOR_FORWARDING = 0


def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """تحويل معرف المستخدم إلى عدد صحيح"""
    try:
        return int(user_id) if user_id is not None and str(user_id).strip().isdigit() else None
    except (TypeError, ValueError):
        return None


class TradeService:
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

    # ==================== دوال مساعدة محسنة ====================
    
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
        return UserRepository(db_session).find_by_telegram_id(uid_int) if uid_int else None

    def _get_or_create_system_user(self, db_session: Session) -> User:
        """الحصول على أو إنشاء مستخدم النظام لتوصيات الظل"""
        system_user = db_session.query(User).filter(User.id == SYSTEM_USER_ID_FOR_FORWARDING).first()
        if not system_user:
            log.info(f"إنشاء مستخدم النظام بالمعرف {SYSTEM_USER_ID_FOR_FORWARDING} للتوصيات المعاد توجيهها")
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

    def format_trade_for_display(self, trade: UserTrade) -> Dict[str, Any]:
        """تنسيق بيانات الصفقة للعرض"""
        return {
            'id': trade.id,
            'asset': trade.asset,
            'side': trade.side,
            'entry': float(trade.entry),
            'stop_loss': float(trade.stop_loss),
            'targets': trade.targets,
            'status': trade.status.value,
            'created_at': trade.created_at.isoformat() if trade.created_at else None,
            'closed_at': trade.closed_at.isoformat() if trade.closed_at else None,
            'pnl_percent': float(trade.pnl_percentage) if trade.pnl_percentage else None,
            'source_recommendation_id': trade.source_recommendation_id
        }

    def format_recommendation_for_display(self, rec: RecommendationEntity) -> Dict[str, Any]:
        """تنسيق بيانات التوصية للعرض"""
        return {
            'id': rec.id,
            'asset': rec.asset.value,
            'side': rec.side.value,
            'entry': rec.entry.value,
            'stop_loss': rec.stop_loss.value,
            'targets': [{'price': t.price, 'close_percent': t.close_percent} for t in rec.targets.values],
            'status': rec.status.value,
            'market': rec.market,
            'order_type': rec.order_type.value,
            'exit_strategy': rec.exit_strategy.value,
            'open_size_percent': rec.open_size_percent,
            'created_at': rec.created_at.isoformat() if rec.created_at else None,
            'activated_at': rec.activated_at.isoformat() if rec.activated_at else None,
            'closed_at': rec.closed_at.isoformat() if rec.closed_at else None,
            'is_shadow_recommendation': rec.analyst_id == SYSTEM_USER_ID_FOR_FORWARDING
        }

    # ==================== دوال الإشعارات ====================

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """استدعاء الدوال المتزامنة وغير المتزامنة بشكل آمن"""
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def notify_card_update(self, rec_entity: RecommendationEntity):
        """تحديث بطاقة التوصية في القنوات"""
        to_delete = []
        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec_entity.id)
            if not published_messages:
                return
                
            log.info("تحديث %d بطاقة بشكل غير متزامن للتوصية #%s...", len(published_messages), rec_entity.id)
            for msg_meta in published_messages:
                try:
                    edit_fn = getattr(self.notifier, "edit_recommendation_card_by_ids", None)
                    if edit_fn is None:
                        log.error("Notifier missing 'edit_recommendation_card_by_ids' method.")
                        continue
                    await self._call_notifier_maybe_async(edit_fn, 
                        channel_id=msg_meta.telegram_channel_id, 
                        message_id=msg_meta.telegram_message_id, 
                        rec=rec_entity)
                except Exception as e:
                    err_text = str(e).lower()
                    if "message to edit not found" in err_text or "message not found" in err_text:
                        log.warning("الرسالة %s للتوصية %s غير موجودة. جدولة الإزالة.", 
                                   msg_meta.telegram_message_id, rec_entity.id)
                        to_delete.append(msg_meta)
                    else:
                        log.error("فشل تحديث البطاقة للتوصية %s في القناة %s: %s", 
                                 rec_entity.id, msg_meta.telegram_channel_id, e, exc_info=True)
            
            for dm in to_delete:
                try:
                    session.delete(dm)
                except Exception:
                    log.exception("فشل حذف PublishedMessage %s", getattr(dm, "id", "<unknown>"))

    def notify_reply(self, rec_id: int, text: str):
        """إرسال رد على التوصية في القنوات"""
        with session_scope() as session:
            published_messages = self.repo.get_published_messages(session, rec_id)
            for msg_meta in published_messages:
                try:
                    post_fn = getattr(self.notifier, "post_notification_reply", None)
                    if post_fn is None:
                        log.error("Notifier missing 'post_notification_reply' method.")
                        continue
                    if inspect.iscoroutinefunction(post_fn):
                        try:
                            loop = asyncio.get_running_loop()
                            asyncio.run_coroutine_threadsafe(
                                post_fn(chat_id=msg_meta.telegram_channel_id, 
                                       message_id=msg_meta.telegram_message_id, 
                                       text=text), loop)
                        except RuntimeError:
                            asyncio.run(post_fn(chat_id=msg_meta.telegram_channel_id, 
                                              message_id=msg_meta.telegram_message_id, 
                                              text=text))
                    else:
                        post_fn(chat_id=msg_meta.telegram_channel_id, 
                              message_id=msg_meta.telegram_message_id, 
                              text=text)
                except Exception as e:
                    log.warning("فشل إرسال إشعار الرد للتوصية #%s إلى القناة %s: %s", 
                               rec_id, msg_meta.telegram_channel_id, e)

    # ==================== التحقق من البيانات ====================

    def _validate_recommendation_data(self, side: str, entry: float, stop_loss: float, targets: List[Dict[str, float]]):
        """التحقق من صحة بيانات التوصية"""
        side_upper = side.upper()
        
        # التحقق من الأسعار الموجبة
        if entry <= 0 or stop_loss <= 0:
            raise ValueError("يجب أن تكون أسعار الدخول ووقف الخسارة موجبة.")
        
        # التحقق من وجود أهداف صحيحة
        if not targets or not all(t.get('price', 0) > 0 for t in targets):
            raise ValueError("مطلوب هدف واحد صالح على الأقل بسعر موجب.")
        
        # التحقق من علاقة السعر مع وقف الخسارة
        if side_upper == "LONG" and stop_loss >= entry:
            raise ValueError("لصفقات الشراء، يجب أن يكون وقف الخسارة < سعر الدخول.")
        if side_upper == "SHORT" and stop_loss <= entry:
            raise ValueError("لصفقات البيع، يجب أن يكون وقف الخسارة > سعر الدخول.")
        
        # التحقق من صحة الأسعار المستهدفة
        for target in targets:
            if side_upper == 'LONG' and target['price'] <= entry:
                raise ValueError(f"سعر الهدف {target['price']} يجب أن يكون أعلى من سعر الدخول لصفقة شراء.")
            if side_upper == 'SHORT' and target['price'] >= entry:
                raise ValueError(f"سعر الهدف {target['price']} يجب أن يكون أقل من سعر الدخول لصفقة بيع.")
        
        # حساب نسبة المخاطرة/العائد
        risk = abs(entry - stop_loss)
        if risk <= 1e-9:
            raise ValueError("لا يمكن أن تكون أسعار الدخول ووقف الخسارة متماثلة.")
        
        if side_upper == "LONG":
            first_target_price = min(t['price'] for t in targets)
        else:
            first_target_price = max(t['price'] for t in targets)
            
        reward = abs(first_target_price - entry)
        min_acceptable_rr = 0.1
        if (reward / risk) < min_acceptable_rr:
            raise ValueError(f"نسبة المخاطرة/العائد منخفضة جداً: {(reward / risk):.3f}. الحد الأدنى المسموح: {min_acceptable_rr}")
        
        # التحقق من تفرد الأسعار المستهدفة
        target_prices = [t['price'] for t in targets]
        if len(target_prices) != len(set(target_prices)):
            raise ValueError("يجب أن تكون أسعار الأهداف فريدة.")
        
        # التحقق من ترتيب الأسعار المستهدفة
        is_long = side_upper == 'LONG'
        sorted_prices = sorted(target_prices, reverse=not is_long)
        if target_prices != sorted_prices:
            raise ValueError("يجب أن تكون الأهداف بترتيب تصاعدي لصفقات الشراء وتنازلي لصفقات البيع.")
        
        # التحقق من نسب الإغلاق
        total_close = sum(float(t.get('close_percent', 0)) for t in targets)
        if total_close > 100.01:
            raise ValueError("لا يمكن أن يتجاوز مجموع نسب الإغلاق المستهدفة 100%.")

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
        
        user = self._get_user_by_telegram_id(session, user_id)
        if not user:
            report["failed"].append({"reason": "المستخدم غير موجود"})
            return rec_entity, report
            
        channels_to_publish = ChannelRepository(session).list_by_analyst(user.id, only_active=True)
        if target_channel_ids is not None:
            channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
            
        if not channels_to_publish:
            reason = "لا توجد قنوات نشطة مرتبطة." if target_channel_ids is None else "لا توجد قنوات محددة نشطة أو مرتبطة."
            report["failed"].append({"reason": reason})
            return rec_entity, report
            
        from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        
        for ch in channels_to_publish:
            success = False
            last_exc = None
            for attempt in range(3):
                try:
                    post_fn = getattr(self.notifier, "post_to_channel", None)
                    if post_fn is None: 
                        raise RuntimeError("Notifier missing 'post_to_channel' method.")
                        
                    res = await self._call_notifier_maybe_async(post_fn, ch.telegram_channel_id, rec_entity, keyboard)
                    if isinstance(res, tuple) and len(res) == 2:
                        publication = PublishedMessage(
                            recommendation_id=rec_entity.id, 
                            telegram_channel_id=res[0], 
                            telegram_message_id=res[1]
                        )
                        session.add(publication)
                        report["success"].append({"channel_id": ch.telegram_channel_id, "message_id": res[1]})
                        success = True
                        break
                    else:
                        raise RuntimeError(f"Notifier returned unsupported response type: {type(res)}")
                except Exception as e:
                    last_exc = e
                    log.warning("محاولة النشر %d فشلت للقناة %s: %s", attempt + 1, ch.telegram_channel_id, e)
                    await asyncio.sleep(0.2 * (attempt + 1))
                    
            if not success:
                err_msg = str(last_exc) if last_exc is not None else "خطأ غير معروف"
                report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": err_msg})
                
        try:
            session.flush()
        except Exception:
            log.exception("فشل حفظ سجلات PublishedMessage.")
            
        return rec_entity, report

    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """إنشاء وتوصية جديدة ونشرها"""
        user = self._get_user_by_telegram_id(db_session, user_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("يسمح فقط للمحللين بإنشاء التوصيات.")

        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        
        order_type_input = kwargs['order_type']
        order_type_str = self._convert_enum_to_string(order_type_input)
        order_type_enum = OrderType(order_type_str)
        
        status, final_entry = (RecommendationStatusEnum.PENDING, kwargs['entry'])
        if order_type_enum == OrderType.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            if live_price is None: 
                raise RuntimeError(f"تعذر جلب السعر الحي لـ {asset}.")
            status, final_entry = RecommendationStatusEnum.ACTIVE, live_price
        
        targets_list = kwargs['targets']
        self._validate_recommendation_data(side, final_entry, kwargs['stop_loss'], targets_list)

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
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None
        )
        
        db_session.add(rec_orm)
        db_session.flush()

        event_type = "CREATED_ACTIVE" if rec_orm.status == RecommendationStatusEnum.ACTIVE.value else "CREATED_PENDING"
        new_event = RecommendationEvent(recommendation_id=rec_orm.id, event_type=event_type, event_data={})
        db_session.add(new_event)
        db_session.flush()
        
        db_session.refresh(rec_orm)

        created_rec_entity = self.repo._to_entity(rec_orm)
        await self.alert_service.update_triggers_for_recommendation(created_rec_entity.id)
        
        final_rec, report = await self._publish_recommendation(db_session, created_rec_entity, user_id, kwargs.get('target_channel_ids'))
        return final_rec, report

    def get_recommendation_for_user(self, db_session: Session, rec_id: int, user_telegram_id: str) -> Optional[RecommendationEntity]:
        """الحصول على توصية محددة للمستخدم"""
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
        """الحصول على الصفقات المفتوحة للمستخدم"""
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
        """الحصول على أحدث الأصول للمستخدم"""
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
        """إلغاء توصية معلقة يدويًا"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("يسمح فقط للمحللين بإلغاء التوصيات.")
            
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو الوصول مرفوض.")
            
        if rec_orm.status != RecommendationStatusEnum.PENDING:
            raise ValueError("يمكن إلغاء التوصيات المعلقة فقط.")
            
        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.updated_at = datetime.now(timezone.utc)
        
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type="CANCELLED_MANUALLY",
            event_data={"cancelled_by": user_telegram_id}
        )
        db_session.add(event)
        
        await self.alert_service.remove_triggers_for_item(rec_id, is_user_trade=False)
        
        log.info(f"✅ تم إلغاء التوصية المعلقة #{rec_id} من قبل المستخدم {user_telegram_id}")
        return self.repo._to_entity(rec_orm)

    async def close_recommendation_for_user_async(self, rec_id: int, user_telegram_id: str, exit_price: float, 
                                                reason: str = "MANUAL_CLOSE", db_session: Session = None) -> RecommendationEntity:
        """إغلاق توصية للمستخدم"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("يسمح فقط للمحللين بإغلاق التوصيات.")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو الوصول مرفوض.")
            
        if rec_orm.status == RecommendationStatusEnum.CLOSED:
            raise ValueError("التوصية مغلقة بالفعل.")
            
        rec_orm.status = RecommendationStatusEnum.CLOSED
        rec_orm.exit_price = exit_price
        rec_orm.closed_at = datetime.now(timezone.utc)
        rec_orm.updated_at = datetime.now(timezone.utc)
        
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
        
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        
        await self.alert_service.remove_triggers_for_item(rec_id, is_user_trade=False)
        
        log.info(f"✅ تم إغلاق التوصية #{rec_id} بالسعر {exit_price} من قبل المستخدم {user_telegram_id}")
        return rec_entity

    async def close_recommendation_at_market_for_user_async(self, rec_id: int, user_telegram_id: str, db_session: Session) -> RecommendationEntity:
        """إغلاق توصية بسعر السوق"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("يسمح فقط للمحللين بإغلاق التوصيات.")
            
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو الوصول مرفوض.")
            
        market_price = await self.price_service.get_cached_price(rec_orm.asset, rec_orm.market, force_refresh=True)
        if not market_price:
            raise ValueError("تعذر جلب سعر السوق الحالي.")
            
        return await self.close_recommendation_for_user_async(rec_id, user_telegram_id, market_price, "MARKET_CLOSE", db_session=db_session)

    async def update_sl_for_user_async(self, rec_id: int, user_telegram_id: str, new_sl: float, db_session: Session) -> RecommendationEntity:
        """تحديث وقف الخسارة لتوصية"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("يسمح فقط للمحللين بتحديث التوصيات.")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو الوصول مرفوض.")
            
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("يمكن تحديث التوصيات النشطة فقط.")
            
        old_sl = rec_orm.stop_loss
        rec_orm.stop_loss = new_sl
        rec_orm.updated_at = datetime.now(timezone.utc)
        
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
        
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        
        log.info(f"✅ تم تحديث وقف الخسارة للتوصية #{rec_id} من {old_sl} إلى {new_sl} من قبل المستخدم {user_telegram_id}")
        return rec_entity

    async def update_targets_for_user_async(self, rec_id: int, user_telegram_id: str, new_targets: List[Dict[str, float]], db_session: Session) -> RecommendationEntity:
        """تحديث الأهداف لتوصية"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user or not self._check_analyst_permission(user):
            raise ValueError("يسمح فقط للمحللين بتحديث التوصيات.")
            
        rec_orm = self.repo.get_for_update(db_session, rec_id)
        if not rec_orm or not self._check_recommendation_ownership(rec_orm, user.id):
            raise ValueError("التوصية غير موجودة أو الوصول مرفوض.")
            
        if rec_orm.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("يمكن تحديث التوصيات النشطة فقط.")
            
        self._validate_recommendation_data(rec_orm.side, float(rec_orm.entry), float(rec_orm.stop_loss), new_targets)
        
        old_targets = rec_orm.targets
        rec_orm.targets = new_targets
        rec_orm.updated_at = datetime.now(timezone.utc)
        
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
        
        rec_entity = self.repo._to_entity(rec_orm)
        await self.notify_card_update(rec_entity)
        
        await self.alert_service.update_triggers_for_recommendation(rec_id)
        
        log.info(f"✅ تم تحديث الأهداف للتوصية #{rec_id} من قبل المستخدم {user_telegram_id}")
        return rec_entity

    # ==================== معالجة الأحداث التلقائية ====================

    async def process_activation_event(self, rec_id: int):
        """معالجة حدث تفعيل التوصية تلقائيًا"""
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
            
            log.info(f"✅ تم التفعيل التلقائي للتوصية #{rec_id}")

    async def process_invalidation_event(self, rec_id: int):
        """معالجة حدث إلغاء التوصية تلقائيًا"""
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
            
            log.info(f"🔄 تم الإلغاء التلقائي للتوصية #{rec_id} (وصول وقف الخسارة قبل الدخول)")

    # ==================== منطق توصية الظل ====================

    async def track_forwarded_trade(self, user_id: str, trade_data: Dict[str, Any], db_session: Session) -> Dict[str, Any]:
        """
        تتبع صفقة معاد توجيهها عن طريق إنشاء "توصية ظل" ثم ربط صفقة المستخدم بها.
        """
        try:
            trader_user = self._get_user_by_telegram_id(db_session, user_id)
            
            if not trader_user:
                return {'success': False, 'error': 'المستخدم غير موجود'}

            # --- ✅ منطق توصية الظل ---
            # 1. الحصول على أو إنشاء مستخدم النظام للصفقات المعاد توجيهها
            system_user = self._get_or_create_system_user(db_session)

            # 2. إنشاء سجل "توصية الظل"
            shadow_rec = Recommendation(
                analyst_id=system_user.id,
                asset=trade_data['asset'],
                side=trade_data['side'],
                entry=float(trade_data['entry']),
                stop_loss=float(trade_data['stop_loss']),
                targets=trade_data['targets'],
                status=RecommendationStatusEnum.ACTIVE, # الصفقات المعاد توجيهها تعتبر نشطة للتتبع
                order_type="MARKET", # افتراض دخول بسعر السوق للتبسيط
                notes="معاد توجيهها من قبل المستخدم.",
                market="Futures",
                is_shadow=True, # ✅ الإصلاح هنا - استخدم is_shadow بدلاً من is_shadow_recommendation
                activated_at=datetime.now(timezone.utc)
            )
            db_session.add(shadow_rec)
            db_session.flush()
            
            # 3. إنشاء UserTrade وربطه بتوصية الظل
            new_trade = UserTrade(
                user_id=trader_user.id,
                source_recommendation_id=shadow_rec.id, # ✅ ربط حاسم
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
            
            # 4. إنشاء حدث للتوصية الظل
            shadow_event = RecommendationEvent(
                recommendation_id=shadow_rec.id,
                event_type="SHADOW_CREATED",
                event_data={
                    "created_for_user": user_id,
                    "original_trade_data": trade_data
                }
            )
            db_session.add(shadow_event)
            
            # خدمة التنبيهات ستلتقط توصية الظل تلقائياً
            # لأنها في حالة نشطة. نحتاج فقط إلى تشغيل تحديث.
            await self.alert_service.build_triggers_index()
            
            log.info(f"✅ تمت إضافة الصفقة المعاد توجيهها #{new_trade.id} للمستخدم {user_id} عبر توصية الظل #{shadow_rec.id}")
            
            return {
                'success': True,
                'trade_id': new_trade.id,
                'shadow_recommendation_id': shadow_rec.id,
                'asset': new_trade.asset,
                'side': new_trade.side,
                'status': 'ADDED'
            }
            
        except Exception as e:
            log.error(f"❌ فشل تتبع الصفقة المعاد توجيهها للمستخدم {user_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    async def close_user_trade_async(self, trade_id: int, user_telegram_id: str, exit_price: float, db_session: Session) -> Dict[str, Any]:
        """إغلاق صفقة مستخدم وإغلاق توصية الظل المرتبطة بها إن وجدت"""
        try:
            user = self._get_user_by_telegram_id(db_session, user_telegram_id)
            if not user:
                return {'success': False, 'error': 'المستخدم غير موجود'}
                
            trade = db_session.query(UserTrade).filter(
                UserTrade.id == trade_id,
                UserTrade.user_id == user.id
            ).first()
            
            if not trade:
                return {'success': False, 'error': 'الصفقة غير موجودة أو لا تملك صلاحية الوصول'}
                
            if trade.status == UserTradeStatus.CLOSED:
                return {'success': False, 'error': 'الصفقة مغلقة بالفعل'}
                
            trade.status = UserTradeStatus.CLOSED
            trade.close_price = exit_price
            trade.closed_at = datetime.now(timezone.utc)
            
            # حساب الربح/الخسارة
            if trade.side.upper() == "LONG":
                pnl_pct = ((exit_price - float(trade.entry)) / float(trade.entry)) * 100
            else:
                pnl_pct = ((float(trade.entry) - exit_price) / float(trade.entry)) * 100
                
            trade.pnl_percentage = pnl_pct
            
            # إذا كانت هناك توصية ظل مرتبطة، قم بإغلاقها أيضاً
            if trade.source_recommendation_id:
                shadow_rec = self.repo.get(db_session, trade.source_recommendation_id)
                if shadow_rec and shadow_rec.status == RecommendationStatusEnum.ACTIVE:
                    shadow_rec.status = RecommendationStatusEnum.CLOSED
                    shadow_rec.exit_price = exit_price
                    shadow_rec.closed_at = datetime.now(timezone.utc)
                    
                    # إنشاء حدث إغلاق لتوصية الظل
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
                    
                    log.info(f"✅ تم إغلاق توصية الظل #{shadow_rec.id} المرتبطة بالصفقة #{trade_id}")
            
            await self.alert_service.build_triggers_index()
            
            log.info(f"✅ تم إغلاق صفقة المستخدم #{trade_id} بالسعر {exit_price} للمستخدم {user_telegram_id}")
            
            return {
                'success': True,
                'trade_id': trade_id,
                'asset': trade.asset,
                'side': trade.side,
                'pnl_percent': pnl_pct,
                'status': 'CLOSED'
            }
            
        except Exception as e:
            log.error(f"❌ فشل إغلاق صفقة المستخدم #{trade_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def get_shadow_recommendations(self, db_session: Session) -> List[Recommendation]:
        """الحصول على جميع توصيات الظل النشطة"""
        return db_session.query(Recommendation).filter(
            Recommendation.analyst_id == SYSTEM_USER_ID_FOR_FORWARDING,
            Recommendation.status == RecommendationStatusEnum.ACTIVE
        ).all()

    def get_user_trades_with_shadows(self, db_session: Session, user_telegram_id: str) -> List[Dict[str, Any]]:
        """الحصول على صفقات المستخدم مع معلومات توصيات الظل المرتبطة"""
        user = self._get_user_by_telegram_id(db_session, user_telegram_id)
        if not user:
            return []
            
        trades = self.repo.get_open_trades_for_trader(db_session, user.id)
        result = []
        
        for trade in trades:
            trade_data = self.format_trade_for_display(trade)
            if trade.source_recommendation_id:
                shadow_rec = self.repo.get(db_session, trade.source_recommendation_id)
                if shadow_rec:
                    trade_data['shadow_recommendation'] = {
                        'id': shadow_rec.id,
                        'status': shadow_rec.status,
                        'created_at': shadow_rec.created_at.isoformat() if shadow_rec.created_at else None,
                        'is_shadow': getattr(shadow_rec, 'is_shadow', False)
                    }
            result.append(trade_data)
            
        return result


__all__ = ['TradeService', 'SYSTEM_USER_ID_FOR_FORWARDING']