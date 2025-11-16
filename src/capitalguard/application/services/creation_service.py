# File: src/capitalguard/application/services/creation_service.py
# Version: v3.0.1-R2
# ✅ THE FIX: (NEW FILE - R2 Architecture)
#    - 1. (NEW) خدمة جديدة ومستقلة تمامًا، تنفذ مبدأ "الأرض الواسعة".
#    - 2. (SoC) تفصل منطق "الإنشاء" (Creation) عن "دورة الحياة" (Lifecycle).
#    - 3. (Core Algorithm) تنقل منطق إنشاء `UserTrade` (من v2) وتنفذ
#       خوارزمية `v3.0` لوضعه في `WATCHLIST` أو `PENDING_ACTIVATION`.
#    - 4. (v3.0.1) إزالة جميع علامات الاقتباس من التعليقات.
# 🎯 IMPACT: هذا الملف هو "مصدر الحقيقة" (SSoT) لجميع عمليات الإنشاء في النظام.

from __future__ import annotations
import logging
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict, Any, Set, Union
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session
from sqlalchemy import select

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
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets

# Type-only imports
if False:
    from .alert_service import AlertService
    from .price_service import PriceService
    from .market_data_service import MarketDataService
    from .lifecycle_service import LifecycleService

logger = logging.getLogger(__name__)

# --- Helper Functions (Moved from TradeService) ---
# هذه الدوال المساعدة ضرورية لعملية الإنشاء والتحقق

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

# --- End Helpers ---


class CreationService:
    """
    [R2 Service]
    مسؤولة حصريًا عن "إنشاء" الكيانات (التوصيات والصفقات) والتحقق من صحتها.
    تنفذ "العقد التشغيلي" للمرحلة R1/R2.
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
        # يتم حقن هذه الخدمات لاحقًا (Circular Dependency)
        self.alert_service: Optional["AlertService"] = None
        self.lifecycle_service: Optional["LifecycleService"] = None

    # --- Validation (Moved from TradeService) ---
    def _validate_recommendation_data(self, side: str, entry: Decimal, stop_loss: Decimal, targets: List[Dict[str, Any]]):
        """
        [الخوارزمية الأساسية]
        التحقق الدلالي الصارم
        """
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
            # ... (rest of validation logic remains) ...

        if not target_prices: raise ValueError("No valid target prices found.")
        if side_upper == "LONG" and stop_loss >= entry: raise ValueError("LONG SL must be < Entry.")
        if side_upper == "SHORT" and stop_loss <= entry: raise ValueError("SHORT SL must be > Entry.")
        if side_upper == "LONG" and any(p <= entry for p in target_prices): raise ValueError("LONG targets must be > Entry.")
        if side_upper == "SHORT" and any(p >= entry for p in target_prices): raise ValueError("SHORT targets must be < Entry.")
        
        risk = abs(entry - stop_loss)
        if risk.is_zero(): raise ValueError("Entry and SL cannot be equal.")
        # ... (rest of validation logic) ...
        logger.debug("Data validation successful.")


    # --- Publishing (Moved from TradeService) ---
    async def _publish_recommendation(self, session: Session, rec_entity: RecommendationEntity, user_db_id: int, target_channel_ids: Optional[Set[int]] = None) -> Tuple[RecommendationEntity, Dict]:
        """
        [Helper]
        منطق النشر الفعلي، يتم استدعاؤه الآن من المهمة الخلفية.
        """
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        channels_to_publish = ChannelRepository(session).list_by_analyst(user_db_id, only_active=True)
        if target_channel_ids is not None:
             channels_to_publish = [ch for ch in channels_to_publish if ch.telegram_channel_id in target_channel_ids]
        if not channels_to_publish:
             report["failed"].append({"reason": "No active channels linked/selected."})
             return rec_entity, report
        
        try:
            from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
        except ImportError:
            public_channel_keyboard = lambda *_: None
            logger.warning("public_channel_keyboard not found.")
        
        keyboard = public_channel_keyboard(rec_entity.id, getattr(self.notifier, "bot_username", None))
        
        tasks = []
        channel_map = {ch.telegram_channel_id: ch for ch in channels_to_publish}
        for channel_id in channel_map.keys():
            tasks.append(asyncio.create_task(self._call_notifier_maybe_async(
                self.notifier.post_to_channel, channel_id, rec_entity, keyboard
            )))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, channel_id in enumerate(channel_map.keys()):
            result = results[i]
            if isinstance(result, Exception):
                logger.exception(f"Failed publish Rec {rec_entity.id} channel {channel_id}: {result}")
                report["failed"].append({"channel_id": channel_id, "reason": str(result)})
            elif isinstance(result, tuple) and len(result) == 2:
                session.add(PublishedMessage(recommendation_id=rec_entity.id, telegram_channel_id=result[0], telegram_message_id=result[1]))
                report["success"].append({"channel_id": channel_id, "message_id": result[1]})
            else:
                reason = f"Notifier unexpected result: {type(result)}"
                logger.error(f"Failed publish Rec {rec_entity.id} channel {channel_id}: {reason}")
                report["failed"].append({"channel_id": channel_id, "reason": reason})

        session.flush()
        return rec_entity, report

    async def _call_notifier_maybe_async(self, fn, *args, **kwargs):
        """Helper to call sync or async notifier functions."""
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        else:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, fn, *args, **kwargs)

    # --- Public API - Create Recommendation (Analyst) ---
    async def create_and_publish_recommendation_async(self, user_id: str, db_session: Session, **kwargs) -> Tuple[Optional[RecommendationEntity], Dict]:
        """
        [Lightweight Creator - ADR-001]
        1. التحقق من الصحة
        2. الحفظ كـ "ظل" (is_shadow=True)
        3. الإرجاع فورًا
        """
        user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not user or user.user_type != UserTypeEntity.ANALYST:
            raise ValueError("Only analysts can create recommendations.")
        
        # --- 1. Data Validation & Preparation ---
        entry_price_in = _to_decimal(kwargs['entry'])
        sl_price = _to_decimal(kwargs['stop_loss'])
        targets_list_in = kwargs['targets']
        targets_list_validated = [{'price': _to_decimal(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_in]
        asset = kwargs['asset'].strip().upper()
        side = kwargs['side'].upper()
        market = kwargs.get('market', 'Futures')
        order_type_enum = OrderTypeEnum[kwargs['order_type'].upper()]
        
        exit_strategy_val = kwargs.get('exit_strategy')
        if exit_strategy_val is None:
            exit_strategy_enum = ExitStrategyEnum.CLOSE_AT_FINAL_TP
        elif isinstance(exit_strategy_val, str):
            exit_strategy_enum = ExitStrategyEnum[exit_strategy_val.upper()]
        elif isinstance(exit_strategy_val, ExitStrategyEnum):
            exit_strategy_enum = exit_strategy_val
        else:
            exit_strategy_enum = ExitStrategyEnum(exit_strategy_val)


        if order_type_enum == OrderTypeEnum.MARKET:
            live_price = await self.price_service.get_cached_price(asset, market, force_refresh=True)
            status, final_entry = RecommendationStatusEnum.ACTIVE, _to_decimal(live_price) if live_price is not None else None
            if final_entry is None or not final_entry.is_finite() or final_entry <= 0:
                raise RuntimeError(f"Could not fetch valid live price for {asset}.")
        else:
            status, final_entry = RecommendationStatusEnum.PENDING, entry_price_in
        
        # --- 2. Core Validation ---
        self._validate_recommendation_data(side, final_entry, sl_price, targets_list_validated)
        targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_validated]
        
        # --- 3. Save as Shadow ---
        rec_orm = Recommendation(
            analyst_id=user.id, asset=asset, side=side, entry=final_entry, 
            stop_loss=sl_price, targets=targets_for_db, order_type=order_type_enum, 
            status=status, market=market, notes=kwargs.get('notes'), 
            exit_strategy=exit_strategy_enum, 
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatusEnum.ACTIVE else None,
            is_shadow=True # [ADR-001] حفظ كـ "ظل"
        )
        
        db_session.add(rec_orm)
        db_session.flush()
        db_session.add(RecommendationEvent(recommendation_id=rec_orm.id, event_type="CREATED_ACTIVE" if status == RecommendationStatusEnum.ACTIVE else "CREATED_PENDING", event_data={'entry': str(final_entry)}))
        
        # الالتزام (Commit) سيحدث بواسطة @uow_transaction
        db_session.flush()
        db_session.refresh(rec_orm)
        
        created_rec_entity = self.repo._to_entity(rec_orm)
        if not created_rec_entity:
            raise RuntimeError(f"Failed to convert new ORM Rec {rec_orm.id} to entity.")

        logger.info(f"Shadow Recommendation #{created_rec_entity.id} created by Analyst {user.id}. Pending background publish.")
        
        # الإرجاع فورًا
        return created_rec_entity, {}

    # --- Public API - Background Publish (Analyst) ---
    async def background_publish_and_index(
        self, 
        rec_id: int, 
        user_db_id: int, 
        target_channel_ids: Optional[Set[int]] = None
    ):
        """
        [Background Task - ADR-001]
        1. النشر البطيء
        2. إضافة الفهرس الذكي (Smart Indexing)
        3. إزالة "الظل" (is_shadow=False)
        """
        if not self.alert_service:
            logger.critical(f"[BG Task Rec {rec_id}]: AlertService is not injected. Aborting.")
            return

        logger.info(f"[BG Task Rec {rec_id}]: Starting background publish and index...")
        try:
            with session_scope() as session:
                # 1. جلب الكائن
                rec_orm = self.repo.get(session, rec_id)
                if not rec_orm:
                    logger.error(f"[BG Task Rec {rec_id}]: ORM object not found in DB.")
                    return

                # 2. النشر
                rec_entity = self.repo._to_entity(rec_orm)
                if not rec_entity:
                     logger.error(f"[BG Task Rec {rec_id}]: Failed to convert ORM to entity.")
                     return
                
                _, report = await self._publish_recommendation(
                    session, rec_entity, user_db_id, target_channel_ids
                )
                
                success_count = len(report.get("success", []))
                if success_count == 0:
                    logger.warning(f"[BG Task Rec {rec_id}]: Failed to publish to any channel. Report: {report.get('failed')}")
                    # سنقوم بالفهرسة على أي حال
                else:
                    logger.info(f"[BG Task Rec {rec_id}]: Published to {success_count} channels.")

                # 3. بناء بيانات الفهرس
                rec_orm_for_trigger = self.repo.get(session, rec_id) # إعادة الجلب بالعلاقات
                trigger_data = self.alert_service.build_trigger_data_from_orm(rec_orm_for_trigger)

                # 4. إضافة الفهرس الذكي
                if trigger_data:
                    await self.alert_service.add_trigger_data(trigger_data)
                else:
                    logger.error(f"[BG Task Rec {rec_id}]: Failed to build trigger data. AlertService will not track this trade!")

                # 5. إزالة الظل (تفعيل نهائي)
                rec_orm.is_shadow = False
                session.commit()
                logger.info(f"[BG Task Rec {rec_id}]: Task complete. Recommendation is now live and indexed.")
                
                # ... (منطق إشعار المستخدم) ...

        except Exception as e:
            logger.error(f"[BG Task Rec {rec_id}]: CRITICAL FAILURE in background task: {e}", exc_info=True)
            # ... (منطق إشعار المستخدم بالفشل) ...


    # --- Public API - Create Trade (Trader) ---
    async def create_trade_from_forwarding_async(
        self, 
        user_id: str, 
        trade_data: Dict[str, Any], 
        original_text: Optional[str], 
        db_session: Session,
        status_to_set: str, # 'WATCHLIST' or 'PENDING_ACTIVATION'
        original_published_at: Optional[datetime],
        channel_info: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        [Core Algorithm - R1]
        الخوارزمية الأساسية لإنشاء صفقة متداول من "إعادة التوجيه الذكية".
        يتم وضعها في طبقة "المتابعة" أو "الانتظار".
        """
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user:
            return {'success': False, 'error': 'User not found'}
        
        try:
            # 1. التحقق الدلالي
            entry_dec = trade_data['entry']
            sl_dec = trade_data['stop_loss']
            targets_list_validated = trade_data['targets']
            self._validate_recommendation_data(trade_data['side'], entry_dec, sl_dec, targets_list_validated)
            targets_for_db = [{'price': str(t['price']), 'close_percent': t.get('close_percent', 0.0)} for t in targets_list_validated]

            # 2. ربط القناة المتابعة (Watched Channel)
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

            # 3. إنشاء صفقة المستخدم (UserTrade)
            new_trade = UserTrade(
                user_id=trader_user.id,
                asset=trade_data['asset'],
                side=trade_data['side'],
                entry=entry_dec,
                stop_loss=sl_dec,
                targets=targets_for_db,
                status=UserTradeStatusEnum[status_to_set], # [Core Algorithm]
                source_forwarded_text=original_text,
                original_published_at=original_published_at,
                watched_channel_id=watched_channel.id if watched_channel else None,
                activated_at=None 
            )
            db_session.add(new_trade)
            db_session.flush()
            
            # 4. إضافة الفهرس الذكي (ADR-001)
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
        """
        [Core Algorithm]
        تسمح للمتداول بـ "تفعيل" توصية محلل رسمية في محفظته.
        """
        trader_user = UserRepository(db_session).find_by_telegram_id(_parse_int_user_id(user_id))
        if not trader_user: return {'success': False, 'error': 'User not found'}
        
        rec_orm = self.repo.get(db_session, rec_id)
        if not rec_orm: return {'success': False, 'error': 'Signal not found'}

        existing_trade = self.repo.find_user_trade_by_source_id(db_session, trader_user.id, rec_id)
        if existing_trade:
            return {'success': False, 'error': 'You are already tracking this signal.'}
        
        try:
            # 1. تحديد الحالة بناءً على حالة التوصية الرسمية
            rec_status = rec_orm.status
            if rec_status == RecommendationStatusEnum.PENDING:
                user_trade_status = UserTradeStatusEnum.PENDING_ACTIVATION
                user_trade_activated_at = None
            elif rec_status == RecommendationStatusEnum.ACTIVE:
                user_trade_status = UserTradeStatusEnum.ACTIVATED
                user_trade_activated_at = rec_orm.activated_at or datetime.now(timezone.utc)
            else: # CLOSED
                return {'success': False, 'error': 'This signal is already closed.'}

            # 2. إنشاء الصفقة
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
            
            # 3. إضافة الفهرس الذكي
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