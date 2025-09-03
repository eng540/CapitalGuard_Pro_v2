# --- START OF FILE: src/capitalguard/infrastructure/db/repository.py ---
from typing import List, Optional
from datetime import datetime
# ✅ --- Import the new Enum ---
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM
from .base import SessionLocal

class RecommendationRepository:
    def _to_entity(self, row: RecommendationORM) -> Recommendation:
        return Recommendation(
            id=row.id, asset=Symbol(row.asset), side=Side(row.side),
            entry=Price(row.entry), stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])), 
            # ✅ Convert string from DB back to Enum
            status=RecommendationStatus(row.status),
            channel_id=row.channel_id, message_id=row.message_id,
            published_at=row.published_at, market=row.market, notes=row.notes,
            user_id=row.user_id, created_at=row.created_at, updated_at=row.updated_at,
            exit_price=row.exit_price, 
            # ✅ Add new timestamp fields
            activated_at=row.activated_at, 
            closed_at=row.closed_at,
        )

    def add(self, rec: Recommendation) -> Recommendation:
        with SessionLocal() as s:
            row = RecommendationORM(
                asset=rec.asset.value, side=rec.side.value,
                entry=rec.entry.value, stop_loss=rec.stop_loss.value,
                targets=rec.targets.values, 
                # ✅ Store the Enum's value (string) in the DB
                status=rec.status.value,
                channel_id=rec.channel_id, message_id=rec.message_id,
                published_at=rec.published_at, market=rec.market,
                notes=rec.notes, user_id=rec.user_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return self._to_entity(row)

    def get(self, rec_id: int) -> Optional[Recommendation]:
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
        with SessionLocal() as s:
            q = s.query(RecommendationORM)
            if symbol:
                q = q.filter(RecommendationORM.asset == symbol.upper())
            if status:
                q = q.filter(RecommendationORM.status == status.upper())
            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def update(self, rec: Recommendation) -> Recommendation:
        if rec.id is None: raise ValueError("Recommendation ID is required for update")
        with SessionLocal() as s:
            row = s.get(RecommendationORM, rec.id)
            if not row: raise ValueError(f"Recommendation with id {rec.id} not found")
            
            row.asset, row.side, row.entry, row.stop_loss, row.targets = \
                rec.asset.value, rec.side.value, rec.entry.value, rec.stop_loss.value, rec.targets.values
            # ✅ Update using Enum value
            row.status = rec.status.value
            row.channel_id, row.message_id, row.published_at = \
                rec.channel_id, rec.message_id, rec.published_at
            row.market, row.notes, row.user_id = \
                rec.market, rec.notes, rec.user_id
            # ✅ Update new timestamp fields
            row.exit_price, row.activated_at, row.closed_at = \
                rec.exit_price, rec.activated_at, rec.closed_at
            row.updated_at = datetime.utcnow()
            
            s.commit()
            s.refresh(row)
            return self._to_entity(row)
            
    def set_channel_message(self, rec_id: int, channel_id: int, message_id: int) -> Recommendation:
        rec = self.get(rec_id)
        if not rec: raise ValueError(f"Recommendation with id {rec_id} not found")
        rec.channel_id = channel_id
        rec.message_id = message_id
        # Published_at is now set during creation, so this is just an update
        return self.update(rec)
# --- END OF FILE ---