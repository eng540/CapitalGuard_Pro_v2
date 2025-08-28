# --- START OF FILE: src/capitalguard/infrastructure/db/repository.py ---
from typing import List, Optional
from sqlalchemy.orm import Session
from datetime import datetime

from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, Base
from .base import engine, SessionLocal

# إنشاء الجداول لأغراض التطوير/الاختبار (احذر استخدامها في الإنتاج إن كان Alembic يدير المايجريشن)
Base.metadata.create_all(bind=engine)


class RecommendationRepository:
    def _to_entity(self, row: RecommendationORM) -> Recommendation:
        return Recommendation(
            id=row.id,
            asset=Symbol(row.asset),
            side=Side(row.side),
            entry=Price(row.entry),
            stop_loss=Price(row.stop_loss),
            targets=Targets(list(row.targets or [])),
            status=row.status,
            channel_id=row.channel_id,
            user_id=row.user_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            exit_price=row.exit_price,
            closed_at=row.closed_at,
        )

    def add(self, rec: Recommendation) -> Recommendation:
        with SessionLocal() as s:
            row = RecommendationORM(
                asset=rec.asset.value,
                side=rec.side.value,
                entry=rec.entry.value,
                stop_loss=rec.stop_loss.value,
                targets=rec.targets.values,
                status=rec.status,
                channel_id=rec.channel_id,
                user_id=rec.user_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return self._to_entity(row)

    def get(self, rec_id: int) -> Optional[Recommendation]:
        with SessionLocal() as s:
            row = s.get(RecommendationORM, rec_id)
            return self._to_entity(row) if row else None

    def list_open(self, channel_id: int | None = None) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM).filter(RecommendationORM.status == "OPEN")
            if channel_id is not None:
                q = q.filter(RecommendationORM.channel_id == channel_id)
            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def list_all(self, channel_id: int | None = None) -> List[Recommendation]:
        with SessionLocal() as s:
            q = s.query(RecommendationORM)
            if channel_id is not None:
                q = q.filter(RecommendationORM.channel_id == channel_id)
            rows = q.order_by(RecommendationORM.created_at.desc()).all()
            return [self._to_entity(r) for r in rows]

    def update(self, rec: Recommendation) -> Recommendation:
        """
        يحدّث سجل توصية موجودة اعتمادًا على قيم كيان الدومين الممرّر.
        """
        if rec.id is None:
            raise ValueError("Recommendation id is required for update")

        with SessionLocal() as s:
            row = s.get(RecommendationORM, rec.id)  # ✅ استخدم rec.id بدل rec_id غير المعرف
            if not row:
                raise ValueError("Recommendation not found")

            # تعيين الحقول
            row.asset = rec.asset.value
            row.side = rec.side.value
            row.entry = rec.entry.value
            row.stop_loss = rec.stop_loss.value
            row.targets = rec.targets.values
            row.status = rec.status
            row.channel_id = rec.channel_id
            row.user_id = rec.user_id
            row.exit_price = rec.exit_price
            row.closed_at = rec.closed_at
            row.updated_at = datetime.utcnow()

            s.commit()
            s.refresh(row)
            return self._to_entity(row)
# --- END OF FILE ---