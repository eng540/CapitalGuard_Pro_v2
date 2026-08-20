from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalImportBatch


class HistoricalOwnerReviewError(ValueError):
    """Raised when a historical batch review violates the owner-review contract."""


class HistoricalOwnerReviewService:
    """Makes batch validation an explicit, auditable owner decision."""

    def review_batch(
        self,
        session: Session,
        *,
        batch_id: int,
        reviewer_user_id: int,
        approved: bool,
        note: str | None = None,
    ) -> HistoricalImportBatch:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalOwnerReviewError("Historical batch does not exist")
        if batch.status not in {"DRY_RUN", "REVIEW_REQUIRED"}:
            raise HistoricalOwnerReviewError("Only a dry-run batch can be reviewed")
        if not reviewer_user_id:
            raise HistoricalOwnerReviewError("reviewer_user_id is required")
        if approved and batch.accepted_records <= 0:
            raise HistoricalOwnerReviewError("Cannot approve a batch with no accepted records")
        metadata = dict(batch.metadata_json or {})
        metadata["owner_review"] = {
            "reviewer_user_id": reviewer_user_id,
            "approved": bool(approved),
            "note": note,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        batch.metadata_json = metadata
        batch.status = "VALIDATED" if approved else "REJECTED"
        session.flush()
        return batch
