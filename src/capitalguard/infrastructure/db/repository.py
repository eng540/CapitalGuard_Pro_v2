# src/capitalguard/infrastructure/db/repository.py (v3.4 - FINAL PRODUCTION READY)
"""
مستودع البيانات - الإصدار النهائي الكامل مع إصلاحات الاستيراد والدعم الكامل للنظام الموحد
"""

import logging
from typing import List, Optional, Any, Union, Dict, Tuple
from types import SimpleNamespace

from sqlalchemy import desc, func, and_, or_
from sqlalchemy.orm import Session, joinedload, selectinload

# ✅ استيراد الكيانات والقيم من المواقع الصحيحة
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType, 
    ExitStrategy
)
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import (
    User, UserType, Channel, Recommendation, RecommendationEvent, 
    PublishedMessage, UserTrade, UserTradeStatus, RecommendationStatusEnum, 
    OrderTypeEnum, ExitStrategyEnum, AnalystProfile, AnalystStats
)

logger = logging.getLogger(__name__)

class UserRepository:
    """مستودع إدارة المستخدمين"""
    
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """البحث عن مستخدم بواسطة معرف التليجرام"""
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_by_id(self, user_id: int) -> Optional[User]:
        """البحث عن مستخدم بواسطة المعرف الداخلي"""
        return self.session.query(User).filter(User.id == user_id).first()

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        """البحث عن مستخدم أو إنشاؤه إذا لم يكن موجوداً"""
        user = self.find_by_telegram_id(telegram_id)
        if user:
            return user
        
        logger.info("إنشاء مستخدم جديد لـ telegram_id=%s", telegram_id)
        new_user = User(
            telegram_user_id=telegram_id,
            first_name=kwargs.get("first_name"),
            username=kwargs.get("username"),
            is_active=False,
            user_type=kwargs.get("user_type", UserType.TRADER)
        )
        self.session.add(new_user)
        self.session.flush()
        return new_user

    def update_user(self, telegram_id: int, **kwargs) -> Optional[User]:
        """تحديث بيانات المستخدم"""
        user = self.find_by_telegram_id(telegram_id)
        if not user:
            return None
        
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)
        
        self.session.flush()
        return user

    def get_analysts(self, only_active: bool = True) -> List[User]:
        """الحصول على قائمة المحللين"""
        query = self.session.query(User).filter(User.user_type == UserType.ANALYST)
        if only_active:
            query = query.filter(User.is_active.is_(True))
        return query.all()

class ChannelRepository:
    """مستودع إدارة القنوات"""
    
    def __init__(self, session: Session):
        self.session = session

    def list_by_analyst(self, analyst_user_id: int, only_active: bool = False) -> List[Channel]:
        """الحصول على قنوات المحلل"""
        query = self.session.query(Channel).filter(Channel.analyst_id == analyst_user_id)
        if only_active:
            query = query.filter(Channel.is_active.is_(True))
        return query.order_by(Channel.created_at.desc()).all()

    def find_by_telegram_id(self, telegram_channel_id: int) -> Optional[Channel]:
        """البحث عن قناة بواسطة معرف التليجرام"""
        return self.session.query(Channel).filter(
            Channel.telegram_channel_id == telegram_channel_id
        ).first()

    def create_channel(self, analyst_id: int, telegram_channel_id: int, **kwargs) -> Channel:
        """إنشاء قناة جديدة"""
        channel = Channel(
            analyst_id=analyst_id,
            telegram_channel_id=telegram_channel_id,
            username=kwargs.get("username"),
            title=kwargs.get("title"),
            is_active=kwargs.get("is_active", True)
        )
        self.session.add(channel)
        self.session.flush()
        return channel

    def update_channel(self, channel_id: int, **kwargs) -> Optional[Channel]:
        """تحديث بيانات القناة"""
        channel = self.session.query(Channel).filter(Channel.id == channel_id).first()
        if not channel:
            return None
        
        for key, value in kwargs.items():
            if hasattr(channel, key):
                setattr(channel, key, value)
        
        self.session.flush()
        return channel

class RecommendationRepository:
    """مستودع إدارة التوصيات والصفقات"""
    
    @staticmethod
    def _to_entity(row: Recommendation) -> Optional[RecommendationEntity]:
        """تحويل نموذج قاعدة البيانات إلى كيان نطاق"""
        if not row: 
            return None
        
        try:
            return RecommendationEntity(
                id=row.id,
                asset=Symbol(row.asset),
                side=Side(row.side),
                entry=Price(float(row.entry)),
                stop_loss=Price(float(row.stop_loss)),
                targets=Targets(row.targets),
                order_type=OrderType(row.order_type.value),
                status=RecommendationStatusEntity[row.status.name],
                market=row.market,
                notes=row.notes,
                user_id=str(row.analyst.telegram_user_id) if row.analyst else None,
                created_at=row.created_at,
                updated_at=row.updated_at,
                exit_price=float(row.exit_price) if row.exit_price is not None else None,
                activated_at=row.activated_at,
                closed_at=row.closed_at,
                exit_strategy=ExitStrategy(row.exit_strategy.value),
                open_size_percent=float(row.open_size_percent) if row.open_size_percent is not None else 100.0,
                events=[SimpleNamespace(
                    event_type=ev.event_type,
                    event_data=ev.event_data,
                    event_timestamp=ev.event_timestamp
                ) for ev in row.events]
            )
        except Exception as e:
            logger.error("خطأ في تحويل التوصية إلى كيان: %s", e, exc_info=True)
            return None

    def get(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        """الحصول على توصية بواسطة المعرف"""
        return session.query(Recommendation).options(
            joinedload(Recommendation.analyst), 
            selectinload(Recommendation.events)
        ).filter(Recommendation.id == rec_id).first()

    def get_for_update(self, session: Session, rec_id: int) -> Optional[Recommendation]:
        """الحصول على توصية مع قفل للتحديث"""
        return session.query(Recommendation).options(
            selectinload(Recommendation.events)
        ).filter(Recommendation.id == rec_id).with_for_update().first()

    def get_events_for_recommendation(self, session: Session, rec_id: int) -> List[RecommendationEvent]:
        """الحصول على أحداث توصية محددة"""
        return session.query(RecommendationEvent).filter(
            RecommendationEvent.recommendation_id == rec_id
        ).order_by(RecommendationEvent.event_timestamp.asc()).all()

    def list_all_active_triggers_data(self, session: Session) -> List[Dict[str, Any]]:
        """الحصول على جميع بيانات المحفزات النشطة (توصيات وصفقات)"""
        # الصفقات النشطة للمستخدمين
        active_user_trades = session.query(UserTrade).options(
            joinedload(UserTrade.user)
        ).filter(
            UserTrade.status == UserTradeStatus.OPEN
        ).all()

        # التوصيات المعلقة
        pending_recommendations = session.query(Recommendation).options(
            joinedload(Recommendation.analyst), 
            selectinload(Recommendation.events)
        ).filter(
            Recommendation.status == RecommendationStatusEnum.PENDING
        ).all()

        # التوصيات النشطة
        active_recommendations = session.query(Recommendation).options(
            joinedload(Recommendation.analyst)
        ).filter(
            Recommendation.status == RecommendationStatusEnum.ACTIVE
        ).all()

        trigger_data = []
        
        # إضافة الصفقات النشطة
        for trade in active_user_trades:
            trigger_data.append({
                "id": trade.id,
                "user_id": str(trade.user.telegram_user_id),
                "asset": trade.asset,
                "side": trade.side,
                "entry": float(trade.entry),
                "stop_loss": float(trade.stop_loss),
                "targets": trade.targets,
                "status": "ACTIVE",
                "is_user_trade": True,
                "processed_events": set(),
                "market": "Futures"  # افتراضي لصفقات المستخدمين
            })

        # إضافة التوصيات المعلقة
        for rec in pending_recommendations:
            trigger_data.append({
                "id": rec.id,
                "user_id": str(rec.analyst.telegram_user_id),
                "asset": rec.asset,
                "side": rec.side,
                "entry": float(rec.entry),
                "stop_loss": float(rec.stop_loss),
                "targets": rec.targets,
                "status": "PENDING",
                "is_user_trade": False,
                "processed_events": {e.event_type for e in rec.events},
                "market": rec.market
            })

        # إضافة التوصيات النشطة
        for rec in active_recommendations:
            trigger_data.append({
                "id": rec.id,
                "user_id": str(rec.analyst.telegram_user_id),
                "asset": rec.asset,
                "side": rec.side,
                "entry": float(rec.entry),
                "stop_loss": float(rec.stop_loss),
                "targets": rec.targets,
                "status": "ACTIVE",
                "is_user_trade": False,
                "processed_events": {e.event_type for e in rec.events},
                "market": rec.market
            })

        return trigger_data
        
    def get_published_messages(self, session: Session, rec_id: int) -> List[PublishedMessage]:
        """الحصول على الرسائل المنشورة لتوصية محددة"""
        return session.query(PublishedMessage).filter(
            PublishedMessage.recommendation_id == rec_id
        ).all()

    def get_open_recs_for_analyst(self, session: Session, analyst_user_id: int) -> List[Recommendation]:
        """الحصول على التوصيات المفتوحة للمحلل"""
        return session.query(Recommendation).filter(
            Recommendation.analyst_id == analyst_user_id,
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE])
        ).order_by(Recommendation.created_at.desc()).all()

    def get_open_trades_for_trader(self, session: Session, trader_user_id: int) -> List[UserTrade]:
        """الحصول على الصفقات المفتوحة للمتداول"""
        return session.query(UserTrade).filter(
            UserTrade.user_id == trader_user_id,
            UserTrade.status == UserTradeStatus.OPEN
        ).order_by(UserTrade.created_at.desc()).all()

    def get_user_trade_by_id(self, session: Session, trade_id: int) -> Optional[UserTrade]:
        """الحصول على صفقة مستخدم بواسطة المعرف"""
        return session.query(UserTrade).filter(UserTrade.id == trade_id).first()

    def get_recent_recommendations(self, session: Session, limit: int = 10) -> List[Recommendation]:
        """الحصول على أحدث التوصيات"""
        return session.query(Recommendation).options(
            joinedload(Recommendation.analyst)
        ).order_by(Recommendation.created_at.desc()).limit(limit).all()

    def get_recommendations_by_status(self, session: Session, status: RecommendationStatusEnum) -> List[Recommendation]:
        """الحصول على التوصيات بحالة محددة"""
        return session.query(Recommendation).options(
            joinedload(Recommendation.analyst)
        ).filter(Recommendation.status == status).order_by(Recommendation.created_at.desc()).all()

    def create_recommendation(self, session: Session, **kwargs) -> Recommendation:
        """إنشاء توصية جديدة"""
        recommendation = Recommendation(**kwargs)
        session.add(recommendation)
        session.flush()
        return recommendation

    def create_user_trade(self, session: Session, **kwargs) -> UserTrade:
        """إنشاء صفقة مستخدم جديدة"""
        trade = UserTrade(**kwargs)
        session.add(trade)
        session.flush()
        return trade

    def add_recommendation_event(self, session: Session, rec_id: int, event_type: str, event_data: Dict = None) -> RecommendationEvent:
        """إضافة حدث لتوصية"""
        event = RecommendationEvent(
            recommendation_id=rec_id,
            event_type=event_type,
            event_data=event_data or {}
        )
        session.add(event)
        session.flush()
        return event

    def get_analyst_performance_stats(self, session: Session, analyst_id: int) -> Dict[str, Any]:
        """الحصول على إحصائيات أداء المحلل"""
        # التوصيات المغلقة
        closed_recommendations = session.query(Recommendation).filter(
            Recommendation.analyst_id == analyst_id,
            Recommendation.status == RecommendationStatusEnum.CLOSED,
            Recommendation.exit_price.isnot(None)
        ).all()

        if not closed_recommendations:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0
            }

        winning_trades = 0
        total_pnl = 0.0

        for rec in closed_recommendations:
            entry = float(rec.entry)
            exit_price = float(rec.exit_price)
            
            if rec.side.upper() == "LONG":
                pnl = ((exit_price - entry) / entry) * 100
            else:
                pnl = ((entry - exit_price) / entry) * 100
            
            total_pnl += pnl
            if pnl > 0:
                winning_trades += 1

        total_trades = len(closed_recommendations)
        losing_trades = total_trades - winning_trades
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': round(win_rate, 2),
            'total_pnl': round(total_pnl, 2),
            'avg_pnl': round(avg_pnl, 2)
        }

    def get_user_trading_stats(self, session: Session, user_id: int) -> Dict[str, Any]:
        """الحصول على إحصائيات تداول المستخدم"""
        # الصفقات المغلقة
        closed_trades = session.query(UserTrade).filter(
            UserTrade.user_id == user_id,
            UserTrade.status == UserTradeStatus.CLOSED,
            UserTrade.pnl_percentage.isnot(None)
        ).all()

        if not closed_trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0
            }

        winning_trades = 0
        total_pnl = 0.0

        for trade in closed_trades:
            pnl = float(trade.pnl_percentage)
            total_pnl += pnl
            if pnl > 0:
                winning_trades += 1

        total_trades = len(closed_trades)
        losing_trades = total_trades - winning_trades
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': round(win_rate, 2),
            'total_pnl': round(total_pnl, 2),
            'avg_pnl': round(avg_pnl, 2)
        }

class AnalystProfileRepository:
    """مستودع إدارة ملفات المحللين"""
    
    def __init__(self, session: Session):
        self.session = session

    def get_by_user_id(self, user_id: int) -> Optional[AnalystProfile]:
        """الحصول على ملف المحلل بواسطة معرف المستخدم"""
        return self.session.query(AnalystProfile).filter(
            AnalystProfile.user_id == user_id
        ).first()

    def create_or_update(self, user_id: int, **kwargs) -> AnalystProfile:
        """إنشاء أو تحديث ملف المحلل"""
        profile = self.get_by_user_id(user_id)
        
        if profile:
            for key, value in kwargs.items():
                if hasattr(profile, key):
                    setattr(profile, key, value)
        else:
            profile = AnalystProfile(user_id=user_id, **kwargs)
            self.session.add(profile)
        
        self.session.flush()
        return profile

    def get_public_profiles(self) -> List[AnalystProfile]:
        """الحصول على ملفات المحللين العامة"""
        return self.session.query(AnalystProfile).options(
            joinedload(AnalystProfile.user)
        ).filter(
            AnalystProfile.is_public.is_(True),
            User.is_active.is_(True)
        ).all()

class AnalystStatsRepository:
    """مستودع إحصائيات المحللين"""
    
    def __init__(self, session: Session):
        self.session = session

    def get_by_analyst_id(self, analyst_profile_id: int) -> Optional[AnalystStats]:
        """الحصول على إحصائيات المحلل"""
        return self.session.query(AnalystStats).filter(
            AnalystStats.analyst_profile_id == analyst_profile_id
        ).first()

    def update_stats(self, analyst_profile_id: int, **kwargs) -> AnalystStats:
        """تحديث إحصائيات المحلل"""
        stats = self.get_by_analyst_id(analyst_profile_id)
        
        if stats:
            for key, value in kwargs.items():
                if hasattr(stats, key):
                    setattr(stats, key, value)
        else:
            stats = AnalystStats(analyst_profile_id=analyst_profile_id, **kwargs)
            self.session.add(stats)
        
        self.session.flush()
        return stats

# تصدير الفئات
__all__ = [
    'UserRepository',
    'ChannelRepository', 
    'RecommendationRepository',
    'AnalystProfileRepository',
    'AnalystStatsRepository'
]