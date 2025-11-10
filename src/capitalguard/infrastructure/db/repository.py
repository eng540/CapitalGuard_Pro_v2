# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/repository.py --- v.10
"""
Repository layer — provides clean data access abstractions.
✅ THE FIX (R1-S1 HOTFIX 9): Replaced undefined `OrderTypeEnum` with correct `OrderTypeEntity`.
   This eliminates the `NameError` during runtime in `list_all_active_triggers_data`.
   Also includes previous indentation and import fixes.
"""

import logging
from typing import List, Optional, Any, Dict
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session, joinedload, selectinload
import sqlalchemy as sa 
from sqlalchemy import and_, or_ 

# Import domain entities and value objects
from capitalguard.domain.entities import (
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
    OrderType as OrderTypeEntity,  # ✅ Correct Domain Enum
    ExitStrategy as ExitStrategyEntity,
    UserType as UserTypeEntity 
)
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side

# Import ORM models
from .models import (
    User, Channel, Recommendation, RecommendationEvent,
    PublishedMessage, UserTrade, 
    RecommendationStatusEnum,
    UserTradeStatusEnum,  # ✅ Correct Enum name
    WatchedChannel,
    ParsingTemplate, ParsingAttempt
)

logger = logging.getLogger(__name__)

# ==========================================================
# USER REPOSITORY
# ==========================================================
class UserRepository:
    """Repository for User entities."""
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_by_id(self, user_id: int) -> Optional[User]:
        return self.session.query(User).filter(User.id == user_id).first()

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        user = self.find_by_telegram_id(telegram_id)
        if user:
            updated = False
            if kwargs.get("first_name") and user.first_name != kwargs["first_name"]:
                user.first_name = kwargs["first_name"]
                updated = True
            if kwargs.get("username") and user.username != kwargs["username"]:
                user.username = kwargs["username"]
                updated = True
            if 'user_type' in kwargs and user.user_type != kwargs['user_type']:
                user.user_type = kwargs['user_type']
                updated = True
            if 'is_active' in kwargs and user.is_active != kwargs['is_active']:
                user.is_active = kwargs['is_active']
                updated = True
            if updated:
                self.session.flush()
            return user

        new_user = User(
            telegram_user_id=telegram_id,
            first_name=kwargs.get("first_name"),
            username=kwargs.get("username"),
            is_active=kwargs.get("is_active", False),
            user_type=kwargs.get("user_type", UserTypeEntity.TRADER),
        )
        self.session.add(new_user)
        self.session.flush()
        return new_user


# ==========================================================
# CHANNEL REPOSITORY
# ==========================================================
class ChannelRepository:
    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id_and_analyst(self, channel_id: int, analyst_id: int) -> Optional[Channel]:
        return self.session.query(Channel).filter(
            Channel.telegram_channel_id == channel_id,
            Channel.analyst_id == analyst_id
        ).one_or_none()

    def list_by_analyst(self, analyst_id: int, only_active: bool = True) -> List[Channel]:
        query = self.session.query(Channel).filter(Channel.analyst_id == analyst_id)
        if only_active:
            query = query.filter(Channel.is_active == True)
        return query.order_by(Channel.created_at.desc()).all()

    def add(self, analyst_id: int, telegram_channel_id: int, username: Optional[str], title: Optional[str]) -> Channel:
        new_channel = Channel(
            analyst_id=analyst_id,
            telegram_channel_id=telegram_channel_id,
            username=username,
            title=title,
            is_active=True,
        )
        self.session.add(new_channel)
        self.session.flush()
        return new_channel

    def delete(self, channel: Channel):
        self.session.delete(channel)
        self.session.flush()


# ==========================================================
# PARSING REPOSITORY
# ==========================================================
class ParsingRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_attempt(self, **kwargs) -> ParsingAttempt:
        attempt = ParsingAttempt(**kwargs)
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def update_attempt(self, attempt_id: int, **kwargs):
        attempt = self.session.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
        if attempt:
            for key, value in kwargs.items():
                setattr(attempt, key, value)
            self.session.flush()

    def get_active_templates(self, user_id: Optional[int] = None) -> List[ParsingTemplate]:
        query = self.session.query(ParsingTemplate).filter(
            sa.or_(
                ParsingTemplate.is_public == True,
                ParsingTemplate.analyst_id == user_id
            )
        ).order_by(
            ParsingTemplate.confidence_score.desc().nullslast(),
            ParsingTemplate.id
        )
        return query.all()

    def add_template(self, **kwargs) -> ParsingTemplate:
        kwargs.setdefault('is_public', False)
        template = ParsingTemplate(**kwargs)
        self.session.add(template)
        self.session.flush()
        return template

    def find_template_by_id(self, template_id: int) -> Optional[ParsingTemplate]:
        return self.session.query(ParsingTemplate).filter(ParsingTemplate.id == template_id).first()


# ==========================================================
# RECOMMENDATION REPOSITORY
# ==========================================================
class RecommendationRepository:
    @staticmethod
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

    @staticmethod
    def _to_entity(row: Recommendation) -> Optional[RecommendationEntity]:
        if not row:
            return None
        try:
            targets_data = row.targets or []
            formatted_targets = [
                {"price": RecommendationRepository._to_decimal(t.get("price")),
                 "close_percent": t.get("close_percent", 0.0)}
                for t in targets_data if t.get("price") is not None
            ]
            entity = RecommendationEntity(
                id=row.id,
                analyst_id=row.analyst_id,
                asset=Symbol(row.asset),
                side=Side(row.side),
                entry=Price(RecommendationRepository._to_decimal(row.entry)),
                stop_loss=Price(RecommendationRepository._to_decimal(row.stop_loss)),
                targets=Targets(formatted_targets),
                order_type=OrderTypeEntity(row.order_type.value),
                status=RecommendationStatusEntity(row.status.value),
                market=row.market,
                notes=row.notes,
                created_at=row.created_at,
                updated_at=row.updated_at,
                exit_price=float(row.exit_price) if row.exit_price is not None else None,
                activated_at=row.activated_at,
                closed_at=row.closed_at,
                open_size_percent=float(row.open_size_percent),
                is_shadow=row.is_shadow,
                events=list(row.events or []),
                exit_strategy=ExitStrategyEntity(row.exit_strategy.value),
            )
            return entity
        except Exception as e:
            logger.error(f"Error translating Recommendation ID {getattr(row, 'id', 'N/A')}: {e}", exc_info=True)
            return None

    def get_all_active_recs(self, session: Session) -> List[Recommendation]:
        return session.query(Recommendation).options(
            selectinload(Recommendation.events),
            joinedload(Recommendation.analyst)
        ).filter(
            Recommendation.status.in_([RecommendationStatusEnum.PENDING, RecommendationStatusEnum.ACTIVE])
        ).all()

    def get_all_active_user_trades(self, session: Session) -> List[UserTrade]:
        return session.query(UserTrade).options(
            joinedload(UserTrade.user)
        ).filter(
            UserTrade.status.in_([
                UserTradeStatusEnum.WATCHLIST,
                UserTradeStatusEnum.PENDING_ACTIVATION,
                UserTradeStatusEnum.ACTIVATED
            ])
        ).all()

    def list_all_active_triggers_data(self, session: Session) -> List[Dict[str, Any]]:
        trigger_data = []
        active_recs = self.get_all_active_recs(session)
        for rec in active_recs:
            try:
                data = {
                    "id": rec.id,
                    "item_type": "recommendation",
                    "user_id": str(rec.analyst.telegram_user_id) if rec.analyst else None,
                    "asset": rec.asset,
                    "entry": self._to_decimal(rec.entry),
                    "stop_loss": self._to_decimal(rec.stop_loss),
                    "targets": rec.targets or [],
                    "status": rec.status,
                    "order_type": rec.order_type,
                    "market": rec.market,
                }
                trigger_data.append(data)
            except Exception as e:
                logger.error(f"Trigger data error Rec #{rec.id}: {e}", exc_info=True)

        active_trades = self.get_all_active_user_trades(session)
        for trade in active_trades:
            try:
                data = {
                    "id": trade.id,
                    "item_type": "user_trade",
                    "user_id": str(trade.user.telegram_user_id) if trade.user else None,
                    "asset": trade.asset,
                    "side": trade.side,
                    "entry": self._to_decimal(trade.entry),
                    "stop_loss": self._to_decimal(trade.stop_loss),
                    "targets": trade.targets or [],
                    "status": trade.status,
                    "order_type": OrderTypeEntity.LIMIT,  # ✅ fixed
                    "market": "Futures",
                }
                trigger_data.append(data)
            except Exception as e:
                logger.error(f"Trigger data error Trade #{trade.id}: {e}", exc_info=True)

        return trigger_data
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/repository.py ---