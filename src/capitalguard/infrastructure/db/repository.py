# --- START OF MODIFIED FILE: src/capitalguard/infrastructure/db/repository.py ---
import logging
from datetime import datetime, timezone
from typing import List, Optional, Any, Union, Dict

import sqlalchemy as sa
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User, Channel, PublishedMessage
from .models.recommendation_event import RecommendationEvent # ✅ Import the new model

from .base import SessionLocal

log = logging.getLogger(__name__)

# ... (UserRepository and ChannelRepository remain unchanged for now) ...
class UserRepository:
    def __init__(self, session: Session): self.session = session
    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()
    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        user = self.find_by_telegram_id(telegram_id)
        if user: return user
        log.info("Creating new user for telegram_id=%s", telegram_id)
        new_user = User(
            telegram_user_id=telegram_id, email=kwargs.get("email") or f"tg{telegram_id}@telegram.local",
            user_type=(kwargs.get("user_type") or "trader"), is_active=True, first_name=kwargs.get("first_name"),
        )
        self.session.add(new_user); self.session.commit(); self.session.refresh(new_user)
        return new_user

class ChannelRepository:
    def __init__(self, session: Session): self.session = session
    def list_by_user(self, user_id: int, only_active: bool = False) -> List[Channel]:
        q = self.session.query(Channel).filter(Channel.user_id == user_id)
        if only_active: q = q.filter(Channel.is_active.is_(True))
        return q.order_by(Channel.created_at.desc()).all()
    # ... (other methods in ChannelRepository are unchanged) ...

# =========================
# Recommendation Repository (HEAVILY MODIFIED)
# =========================
class RecommendationRepository:
    # --- Helpers ---
    @staticmethod
    def _coerce_enum(value: Any, enum_cls): return value if isinstance(value, enum_cls) else enum_cls(value)
    @staticmethod
    def _as_telegram_str(user_id: Optional[Union[int, str]]) -> Optional[str]: return str(user_id) if user_id is not None else None
    def _to_entity(self, row: RecommendationORM) -> Optional[Recommendation]:
        if not row: return None
        telegram_user_id = self._as_telegram_str(row.user.telegram_user_id) if getattr(row, "user", None) else None
        return Recommendation(
            id=row.id, asset=Symbol(row.asset), side=self._coerce_enum(row.side, Side),
            entry=Price(row.entry), stop_loss=Price(row.stop_loss), targets=Targets(list(row.targets or [])),
            order_type=self._coerce_enum(row.order_type, OrderType), status=self._coerce_enum(row.status, RecommendationStatus),
            channel_id=row.channel_id, message_id=row.message_id, published_at=row.published_at,
            market=row.market, notes=row.notes, user_id=telegram_user_id, created_at=row.created_at,
            updated_at=row.updated_at, exit_price=row.exit_price, activated_at=row.activated_at,
            closed_at=row.closed_at, alert_meta=dict(row.alert_meta or {}),
            highest_price_reached=row.highest_price_reached,
            lowest_price_reached=row.lowest_price_reached,
        )

    # --- Write Operations (Now with Event Logging) ---
    def add_with_event(self, rec: Recommendation) -> Recommendation:
        """
        Adds a new recommendation and logs the 'CREATE' event in a single transaction.
        """
        if not rec.user_id or not str(rec.user_id).isdigit():
            raise ValueError("A valid user_id (Telegram ID) is required.")
        
        with SessionLocal() as s:
            try:
                user = UserRepository(s).find_or_create(int(rec.user_id))
                
                row = RecommendationORM(
                    user_id=user.id, asset=rec.asset.value, side=rec.side.value,
                    entry=rec.entry.value, stop_loss=rec.stop_loss.value, targets=rec.targets.values,
                    order_type=rec.order_type, status=rec.status, market=rec.market,
                    notes=rec.notes, activated_at=rec.activated_at, alert_meta=rec.alert_meta,
                    highest_price_reached=rec.entry.value if rec.status == RecommendationStatus.ACTIVE else None,
                    lowest_price_reached=rec.entry.value if rec.status == RecommendationStatus.ACTIVE else None,
                )
                s.add(row)
                s.flush()

                create_event = RecommendationEvent(
                    recommendation_id=row.id,
                    event_type='CREATE',
                    event_timestamp=row.created_at,
                    event_data={
                        'entry': rec.entry.value,
                        'sl': rec.stop_loss.value,
                        'targets': rec.targets.values,
                        'order_type': rec.order_type.value
                    }
                )
                s.add(create_event)
                
                s.commit()
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception as e:
                s.rollback()
                log.error("Failed to add recommendation with event: %s", e, exc_info=True)
                raise

    def update_with_event(self, rec: Recommendation, event_type: str, event_data: Dict[str, Any]) -> Recommendation:
        """
        Updates a recommendation's state and logs a corresponding event in a single transaction.
        """
        if rec.id is None: raise ValueError("Recommendation ID is required for update")
        
        with SessionLocal() as s:
            try:
                row = s.query(RecommendationORM).filter(RecommendationORM.id == rec.id).with_for_update().first()
                if not row: raise ValueError(f"Recommendation #{rec.id} not found")

                # Update state
                row.status = rec.status
                row.stop_loss = rec.stop_loss.value
                row.targets = rec.targets.values
                row.notes = rec.notes
                row.exit_price = rec.exit_price
                row.activated_at = rec.activated_at
                row.closed_at = rec.closed_at
                row.alert_meta = rec.alert_meta
                row.highest_price_reached = rec.highest_price_reached
                row.lowest_price_reached = rec.lowest_price_reached
                
                new_event = RecommendationEvent(
                    recommendation_id=row.id,
                    event_type=event_type,
                    event_data=event_data
                )
                s.add(new_event)
                
                s.commit()
                s.refresh(row, attribute_names=["user"])
                return self._to_entity(row)
            except Exception as e:
                s.rollback()
                log.error("Failed to update recommendation with event: %s", e, exc_info=True)
                raise

    # --- Read Operations ---
    def get(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as s:
            row = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(RecommendationORM.id == rec_id).first()
            return self._to_entity(row)

    def list_open(self) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM).options(joinedload(RecommendationORM.user)).filter(
                or_(RecommendationORM.status == RecommendationStatus.PENDING, RecommendationORM.status == RecommendationStatus.ACTIVE)
            )
            return [self._to_entity(r) for r in q.order_by(RecommendationORM.created_at.desc()).all()]

    def check_if_event_exists(self, rec_id: int, event_type: str) -> bool:
        with SessionLocal() as s:
            return s.query(RecommendationEvent).filter_by(recommendation_id=rec_id, event_type=event_type).first() is not None

    # --- Publication Data (Unchanged) ---
    def get_published_messages(self, rec_id: int) -> List[PublishedMessage]:
        with SessionLocal() as s:
            return s.query(PublishedMessage).filter(PublishedMessage.recommendation_id == rec_id).all()
    def save_published_messages(self, messages_data: List[Dict[str, Any]]) -> None:
        if not messages_data: return
        with SessionLocal() as s:
            s.bulk_insert_mappings(PublishedMessage, messages_data); s.commit()
    def update_legacy_publication_fields(self, rec_id: int, first_pub_data: Dict[str, Any]) -> None:
        with SessionLocal() as s:
            s.query(RecommendationORM).filter(RecommendationORM.id == rec_id).update({
                'channel_id': first_pub_data['telegram_channel_id'],
                'message_id': first_pub_data['telegram_message_id'],
                'published_at': datetime.now(timezone.utc)
            }); s.commit()
# --- END OF MODIFIED FILE ---