#// --- START: src/capitalguard/infrastructure/db/repository.py ---
import logging
from typing import List, Optional
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User
from .base import SessionLocal

log = logging.getLogger(__name__)

class RecommendationRepository:
    def _to_entity(self, row: RecommendationORM) -> Recommendation:
        """Maps ORM object to domain entity, ensuring user_id is the Telegram ID."""
        return Recommendation(
            id=row.id,
            asset=Symbol(row.asset),
            side=Side(row.side),
            entry=Price(row.entry),
            stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])),
            order_type=OrderType(row.order_type),
            status=RecommendationStatus(row.status),
            channel_id=row.channel_id,
            message_id=row.message_id,
            published_at=row.published_at,
            market=row.market,
            notes=row.notes,
            user_id=str(row.user.telegram_user_id) if row.user else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
            exit_price=row.exit_price,
            activated_at=row.activated_at,
            closed_at=row.closed_at,
            alert_meta=dict(row.alert_meta or {}),
        )

    def find_or_create_user(self, telegram_id: int, **kwargs) -> User:
        """Finds a user by telegram_id, or creates them if they don't exist."""
        with SessionLocal() as s:
            user = s.query(User).filter(User.telegram_user_id == telegram_id).first()
            if user:
                return user
            
            log.info(f"Creating new user for telegram_id: {telegram_id}")
            new_user = User(telegram_user_id=telegram_id, **kwargs)
            s.add(new_user)
            s.commit()
            s.refresh(new_user)
            return new_user

    def add(self, rec: Recommendation) -> Recommendation:
        """Adds a new Recommendation, linking it to an existing user."""
        if not rec.user_id or not rec.user_id.isdigit():
            raise ValueError("A valid user_id (Telegram ID) is required to create a recommendation.")
            
        with SessionLocal() as s:
            user = self.find_or_create_user(int(rec.user_id))
            
            row = RecommendationORM(
                user_id=user.id, # Use the integer primary key for the FK
                asset=rec.asset.value, side=rec.side.value, entry=rec.entry.value,
                stop_loss=rec.stop_loss.value, targets=rec.targets.values,
                order_type=rec.order_type, status=rec.status, channel_id=rec.channel_id,
                message_id=rec.message_id, published_at=rec.published_at,
                market=rec.market, notes=rec.notes, activated_at=rec.activated_at,
                alert_meta=rec.alert_meta,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return self._to_entity(row)

    def get_by_id_for_user(self, rec_id: int, user_id: int) -> Optional[Recommendation]:
        """Gets a single recommendation only if it belongs to the specified user."""
        with SessionLocal() as s:
            user = self.find_or_create_user(user_id)
            row = (
                s.query(RecommendationORM)
                .options(joinedload(RecommendationORM.user))
                .filter(RecommendationORM.id == rec_id, RecommendationORM.user_id == user.id)
                .first()
            )
            return self._to_entity(row) if row else None

    def list_open_for_user(self, user_id: int, **filters) -> List[Recommendation]:
        """Lists open recommendations scoped to a specific user."""
        with SessionLocal() as s:
            user = self.find_or_create_user(user_id)
            q = s.query(RecommendationORM).filter(
                RecommendationORM.user_id == user.id,
                or_(
                    RecommendationORM.status == RecommendationStatus.PENDING,
                    RecommendationORM.status == RecommendationStatus.ACTIVE
                )
            )
            # Apply optional filters
            # ... (add symbol/side/status filter logic here if needed) ...
            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def list_all_for_user(self, user_id: int, **filters) -> List[Recommendation]:
        """Lists all recommendations scoped to a specific user."""
        with SessionLocal() as s:
            user = self.find_or_create_user(user_id)
            q = s.query(RecommendationORM).filter(RecommendationORM.user_id == user.id)
            # Apply optional filters
            # ... (add symbol/side/status filter logic here if needed) ...
            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def update(self, rec: Recommendation) -> Recommendation:
        if rec.id is None or not rec.user_id:
            raise ValueError("Recommendation ID and User ID are required for update")
        with SessionLocal() as s:
            user = self.find_or_create_user(int(rec.user_id))
            row = s.query(RecommendationORM).filter(
                RecommendationORM.id == rec.id,
                RecommendationORM.user_id == user.id
            ).first()

            if not row:
                raise ValueError(f"Recommendation {rec.id} not found for user {rec.user_id}")

            # Update fields
            row.status = rec.status
            row.exit_price = rec.exit_price
            row.closed_at = rec.closed_at
            # ... (add other updatable fields here) ...
            s.commit()
            s.refresh(row)
            return self._to_entity(row)
}
#/ --- END: src/capitalguard/infrastructure/db/repository.py ---