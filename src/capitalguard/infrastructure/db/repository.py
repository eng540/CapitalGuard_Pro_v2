# --- START OF FILE: src/capitalguard/infrastructure/db/repository.py ---
import logging
from typing import List, Optional
import sqlalchemy as sa

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM
from .base import SessionLocal

log = logging.getLogger(__name__)

class RecommendationRepository:
    def _to_entity(self, row: RecommendationORM) -> Recommendation:
        """Map ORM row -> Domain entity (يتعامل بأمان مع Enum أو قيمة نصية)."""
        status = row.status if isinstance(row.status, RecommendationStatus) else RecommendationStatus(row.status)
        order_type = row.order_type if isinstance(row.order_type, OrderType) else OrderType(row.order_type)
        return Recommendation(
            id=row.id,
            asset=Symbol(row.asset),
            side=Side(row.side),
            entry=Price(row.entry),
            stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])),
            order_type=order_type,
            status=status,
            channel_id=row.channel_id,
            message_id=row.message_id,
            published_at=row.published_at,
            market=row.market,
            notes=row.notes,
            user_id=str(row.user_id) if row.user_id is not None else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
            exit_price=row.exit_price,
            activated_at=row.activated_at,
            closed_at=row.closed_at,
        )

    def add(self, rec: Recommendation) -> Recommendation:
        """Adds a new Recommendation, relying on DB server defaults for timestamps."""
        with SessionLocal() as s:
            try:
                row = RecommendationORM(
                    asset=rec.asset.value,
                    side=rec.side.value,
                    entry=rec.entry.value,
                    stop_loss=rec.stop_loss.value,
                    targets=rec.targets.values,
                    order_type=rec.order_type,  # Enum(OrderType)
                    status=rec.status,          # Enum(RecommendationStatus)
                    channel_id=rec.channel_id,
                    message_id=rec.message_id,
                    published_at=rec.published_at,
                    market=rec.market,
                    notes=rec.notes,
                    user_id=rec.user_id,
                    activated_at=rec.activated_at,
                    # created_at / updated_at تُدار بواسطة قاعدة البيانات
                )
                s.add(row)
                s.commit()
                s.refresh(row)
                return self._to_entity(row)
            except Exception as e:
                log.error("❌ Failed to add recommendation. Rolling back. Error: %s", e, exc_info=True)
                s.rollback()
                raise

    def get(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as s:
            row = s.get(RecommendationORM, rec_id)
            return self._to_entity(row) if row else None

    def list_open(self, symbol: Optional[str] = None) -> List[Recommendation]:
        # ✅ IMPROVEMENT: Added optional symbol filter for high-performance lookups (e.g., by the watcher).
        with SessionLocal() as s:
            q = (
                s.query(RecommendationORM)
                .filter(RecommendationORM.status.in_([RecommendationStatus.PENDING, RecommendationStatus.ACTIVE]))
            )
            if symbol:
                q = q.filter(RecommendationORM.asset == symbol.upper())
            
            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM)
            if symbol:
                q = q.filter(RecommendationORM.asset == symbol.upper())
            if status:
                try:
                    status_enum = RecommendationStatus(status.upper())
                    q = q.filter(RecommendationORM.status == status_enum)
                except ValueError:
                    log.warning("Invalid status filter provided: %s", status)

            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def update(self, rec: Recommendation) -> Recommendation:
        """Update existing recommendation; timestamps handled by DB trigger/onupdate."""
        if rec.id is None:
            raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            try:
                row = s.get(RecommendationORM, rec.id)
                if not row:
                    raise ValueError(f"Recommendation with id {rec.id} not found")

                row.asset = rec.asset.value
                row.side = rec.side.value
                row.entry = rec.entry.value
                row.stop_loss = rec.stop_loss.value
                row.targets = rec.targets.values
                row.order_type = rec.order_type
                row.status = rec.status
                row.channel_id = rec.channel_id
                row.message_id = rec.message_id
                row.published_at = rec.published_at
                row.market = rec.market
                row.notes = rec.notes
                row.user_id = rec.user_id
                row.exit_price = rec.exit_price
                row.activated_at = rec.activated_at
                row.closed_at = rec.closed_at
                # updated_at يُدار تلقائياً من قاعدة البيانات

                s.commit()
                s.refresh(row)
                return self._to_entity(row)
            except Exception as e:
                log.error("❌ Failed to update recommendation #%s. Rolling back. Error: %s", rec.id, e, exc_info=True)
                s.rollback()
                raise

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        """Return most recently used unique assets for a user (ordered by last created_at)."""
        with SessionLocal() as s:
            subquery = (
                s.query(
                    RecommendationORM.asset,
                    sa.func.max(RecommendationORM.created_at).label("max_created_at"),
                )
                .filter(RecommendationORM.user_id == user_id)
                .group_by(RecommendationORM.asset)
                .subquery()
            )
            results = (
                s.query(subquery.c.asset)
                .order_by(subquery.c.max_created_at.desc())
                .limit(limit)
                .all()
            )
            return [r[0] for r in results]
# --- END OF FILE ---