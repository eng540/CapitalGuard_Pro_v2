# --- START OF FILE: src/capitalguard/infrastructure/db/repository.py ---
import logging
from typing import List, Optional
import sqlalchemy as sa
from sqlalchemy import or_

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User # Import the new User model
from .base import SessionLocal

log = logging.getLogger(__name__)

class RecommendationRepository:
    def _to_entity(self, row: RecommendationORM) -> Recommendation:
        """Maps ORM object to domain entity, translating the user ID back to string."""
        return Recommendation(
            id=row.id,
            asset=Symbol(row.asset),
            side=Side(row.side),
            entry=Price(row.entry),
            stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])),
            order_type=OrderType(row.order_type),
            status=RecommendationStatus(row.status),
            channel_id=row.channel_id, message_id=row.message_id, published_at=row.published_at,
            market=row.market, notes=row.notes,
            # Translate back: if a user is linked, return their telegram_user_id as a string.
            user_id=str(row.user.telegram_user_id) if row.user else None,
            created_at=row.created_at, updated_at=row.updated_at, exit_price=row.exit_price,
            activated_at=row.activated_at, closed_at=row.closed_at,
            alert_meta=dict(row.alert_meta or {}),
        )

    def add(self, rec: Recommendation) -> Recommendation:
        """Adds a new Recommendation, linking it to an existing user if possible."""
        with SessionLocal() as s:
            try:
                user_db_id = None
                # The domain layer provides a string (telegram_user_id). We need to find the integer PK.
                if rec.user_id and rec.user_id.isdigit():
                    telegram_id = int(rec.user_id)
                    user_obj = s.query(User).filter(User.telegram_user_id == telegram_id).first()
                    if user_obj:
                        user_db_id = user_obj.id
                    else:
                        # User doesn't exist yet, we can create them on-the-fly.
                        new_user = User(telegram_user_id=telegram_id, user_type='analyst')
                        s.add(new_user)
                        s.flush() # Flush to get the new ID
                        user_db_id = new_user.id
                        log.info(f"Created new user on-the-fly for telegram_id: {telegram_id}")
                
                row = RecommendationORM(
                    asset=rec.asset.value, side=rec.side.value, entry=rec.entry.value,
                    stop_loss=rec.stop_loss.value, targets=rec.targets.values,
                    order_type=rec.order_type, status=rec.status, channel_id=rec.channel_id,
                    message_id=rec.message_id, published_at=rec.published_at,
                    market=rec.market, notes=rec.notes, activated_at=rec.activated_at,
                    alert_meta=rec.alert_meta, user_id=user_db_id
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return self._to_entity(row)
            except Exception as e:
                log.error("Failed to add recommendation. Rolling back. Error: %s", e, exc_info=True)
                s.rollback()
                raise

    def get(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as s:
            row = s.get(RecommendationORM, rec_id)
            return self._to_entity(row) if row else None

    # ... (list_open and list_all remain largely the same, no direct user interaction) ...
    def list_open(
        self,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Recommendation]:
        """Enhanced to support filtering by side and status, and partial matching for symbol."""
        with SessionLocal() as s:
            q = s.query(RecommendationORM).filter(
                or_(
                    RecommendationORM.status == RecommendationStatus.PENDING,
                    RecommendationORM.status == RecommendationStatus.ACTIVE,
                )
            )

            if symbol:
                q = q.filter(RecommendationORM.asset.ilike(f"%{symbol.upper()}%"))
            if side:
                q = q.filter(RecommendationORM.side == side.upper())
            if status:
                try:
                    status_enum = RecommendationStatus(status.upper())
                    q = q.filter(RecommendationORM.status == status_enum)
                except ValueError:
                    log.warning("Invalid status filter provided to list_open: %s", status)

            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM)
            if symbol:
                q = q.filter(RecommendationORM.asset.ilike(f"%{symbol.upper()}%"))
            if status:
                try:
                    status_enum = RecommendationStatus(status.upper())
                    q = q.filter(RecommendationORM.status == status_enum)
                except ValueError:
                    log.warning("Invalid status filter provided: %s", status)

            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def update(self, rec: Recommendation) -> Recommendation:
        if rec.id is None: raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            try:
                row = s.get(RecommendationORM, rec.id)
                if not row: raise ValueError(f"Recommendation with id {rec.id} not found")

                # Update all fields except user_id, which is immutable after creation
                row.asset = rec.asset.value; row.side = rec.side.value; row.entry = rec.entry.value
                row.stop_loss = rec.stop_loss.value; row.targets = rec.targets.values
                row.order_type = rec.order_type; row.status = rec.status
                row.channel_id = rec.channel_id; row.message_id = rec.message_id
                row.published_at = rec.published_at; row.market = rec.market
                row.notes = rec.notes; row.exit_price = rec.exit_price
                row.activated_at = rec.activated_at; row.closed_at = rec.closed_at
                row.alert_meta = rec.alert_meta

                s.commit()
                s.refresh(row)
                return self._to_entity(row)
            except Exception as e:
                log.error("Failed to update recommendation #%s. Rolling back. Error: %s", rec.id, e, exc_info=True)
                s.rollback()
                raise

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        if not user_id or not user_id.isdigit(): return []
        with SessionLocal() as s:
            # Find the user's integer ID first
            user = s.query(User).filter(User.telegram_user_id == int(user_id)).first()
            if not user: return []
            
            subquery = (
                s.query(RecommendationORM.asset, sa.func.max(RecommendationORM.created_at).label("max_created_at"))
                .filter(RecommendationORM.user_id == user.id) # Query by the integer foreign key
                .group_by(RecommendationORM.asset)
                .subquery()
            )
            results = (s.query(subquery.c.asset).order_by(subquery.c.max_created_at.desc()).limit(limit).all())
            return [r[0] for r in results]
# --- END OF FILE ---