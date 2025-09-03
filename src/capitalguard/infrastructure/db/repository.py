# --- START OF FILE: src/capitalguard/infrastructure/db/repository.py ---
from typing import List, Optional
from datetime import datetime
# ✅ --- Import OrderType as well ---
from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM
from .base import SessionLocal

class RecommendationRepository:
    def _to_entity(self, row: RecommendationORM) in -> Recommendation:
        """Converts a SQLAlchemy ORM row to a domain Recommendation entity."""
        return Recommendation(
            id=row.id, asset=Symbol(row.asset), side=Side(row.side),
            entry=Price(row.entry), stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])),
            # ✅ --- FIX: Read and convert order_type from the DB row ---
            order_type=OrderType(row.order_type),
            status=RecommendationStatus(row.status),
            channel_id=row.channel_id, message_id=row.message_id,
            published_at=row.published_at, market=row.market, notes=row.notes,
            user_id=row.user_id, created_at=row.created_at, updated_at=row.updated_at,
            exit_price=row.exit_price,
            activated_at=row.activated_at,
            closed_at=row.closed_at,
        )

    def add(self, rec: Recommendation) in -> Recommendation:
        """Adds a new Recommendation to the database."""
        with SessionLocal() as s:
            row = RecommendationORM(
                asset=rec.asset.value, side=rec.side.value,
                entry=rec.entry.value, stop_loss=rec.stop_loss.value,
                targets=rec.targets.values,
                # ✅ --- FIX: Provide the order_type value when creating the ORM object ---
                order_type=rec.order_type.value,
                status=rec.status.value,
                channel_id=rec.channel_id, message_id=rec.message_id,
                published_at=rec.published_at, market=rec.market,
                notes=rec.notes, user_id=rec.user_id,
                activated_at=rec.activated_at # Make sure to save activation time if present
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return self._to_entity(row)

    def get(self, rec_id: int) -> Optional[Recommendation]:
        """Gets a single recommendation by its ID."""
        with SessionLocal() as s:
            row = s.get(RecommendationORM, rec_id)
            return self._to_entity(row) if row else None

    def list_open(self) -> List[Recommendation]:
        """Lists recommendations that are either PENDING or ACTIVE."""
        with SessionLocal() as s:
            rows = s.query(RecommendationORM).filter(
                RecommendationORM.status.in_([RecommendationStatus.PENDING.value, RecommendationStatus.ACTIVE.value])
            ).order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        """Lists all recommendations, with optional filters."""
        with SessionLocal() as s:
            q = s.query(RecommendationORM)
            if symbol: q = q.filter(RecommendationORM.asset == symbol.upper())
            if status: q = q.filter(RecommendationORM.status == status.upper())
            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def update(self, rec: Recommendation) in -> Recommendation:
        """Updates an existing recommendation in the database."""
        if rec.id is None: raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            row = s.get(RecommendationORM, rec.id)
            if not row: raise ValueError(f"Recommendation with id {rec.id} not found")
            
            row.asset = rec.asset.value; row.side = rec.side.value; row.entry = rec.entry.value
            row.stop_loss = rec.stop_loss.value; row.targets = rec.targets.values
            # ✅ --- FIX: Update order_type and status using their .value attribute ---
            row.order_type = rec.order_type.value
            row.status = rec.status.value
            row.channel_id = rec.channel_id; row.message_id = rec.message_id; row.published_at = rec.published_at
            row.market = rec.market; row.notes = rec.notes; row.user_id = rec.user_id
            row.exit_price = rec.exit_price; row.activated_at = rec.activated_at; row.closed_at = rec.closed_at
            row.updated_at = datetime.utcnow()
            
            s.commit()
            s.refresh(row)
            return self._to_entity(row)
# --- END OF FILE ---