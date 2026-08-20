from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.application.services.historical_evidence_ingestion_service import HistoricalEvidenceIngestionService
from capitalguard.application.services.historical_owner_review_service import HistoricalOwnerReviewService
from capitalguard.config import settings
from capitalguard.infrastructure.db.models import HistoricalImportBatch, WebCommandAudit
from capitalguard.infrastructure.db.repository import UserRepository


class WebCommandError(ValueError):
    """Raised when a privileged Web command violates the Core command boundary."""


class WebCommandService:
    REVIEW = "HISTORICAL_OWNER_REVIEW"
    INGEST = "HISTORICAL_EVIDENCE_INGEST"

    @staticmethod
    def _require_owner(session: Session, actor_telegram_id: int):
        configured_owner = str(settings.TELEGRAM_ADMIN_CHAT_ID or "")
        if not configured_owner or configured_owner != str(actor_telegram_id):
            raise WebCommandError("Owner authorization required")
        user = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if user is None:
            raise WebCommandError("Owner identity does not exist in Core")
        return user

    @staticmethod
    def _fingerprint(command_type: str, actor_telegram_id: int, batch_id: int, payload: dict) -> str:
        raw = json.dumps({"command_type": command_type, "actor": actor_telegram_id, "batch": batch_id, "payload": payload}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _replay_or_reject(session: Session, *, idempotency_key: str, request_hash: str) -> dict | None:
        existing = session.execute(select(WebCommandAudit).where(WebCommandAudit.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise WebCommandError("Idempotency key cannot be reused with a different command")
        return dict(existing.response_json or {})

    @staticmethod
    def _record(session: Session, *, idempotency_key: str, command_type: str, actor_user_id: int, batch_id: int, request_hash: str, response: dict) -> None:
        session.add(WebCommandAudit(
            idempotency_key=idempotency_key,
            command_type=command_type,
            actor_user_id=actor_user_id,
            target_type="HISTORICAL_IMPORT_BATCH",
            target_id=batch_id,
            request_hash=request_hash,
            status="COMPLETED",
            response_json=response,
        ))
        session.flush()

    def list_reviewable_batches(self, session: Session, *, actor_telegram_id: int) -> list[dict]:
        self._require_owner(session, actor_telegram_id)
        batches = session.execute(
            select(HistoricalImportBatch)
            .where(HistoricalImportBatch.status.in_(("DRY_RUN", "REVIEW_REQUIRED", "VALIDATED", "EVIDENCE_INGESTED")))
            .order_by(HistoricalImportBatch.created_at.desc())
            .limit(100)
        ).scalars().all()
        return [
            {
                "id": batch.id,
                "ref": batch.batch_ref,
                "status": batch.status,
                "source_kind": batch.source_kind,
                "total_records": batch.total_records,
                "accepted_records": batch.accepted_records,
                "rejected_records": batch.rejected_records,
                "created_at": batch.created_at.isoformat() if batch.created_at else None,
                "owner_review": (batch.metadata_json or {}).get("owner_review"),
            }
            for batch in batches
        ]

    def review_batch(self, session: Session, *, actor_telegram_id: int, batch_id: int, approved: bool, note: str | None, idempotency_key: str) -> dict:
        owner = self._require_owner(session, actor_telegram_id)
        request_hash = self._fingerprint(self.REVIEW, actor_telegram_id, batch_id, {"approved": approved, "note": note})
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        batch = HistoricalOwnerReviewService().review_batch(
            session,
            batch_id=batch_id,
            reviewer_user_id=owner.id,
            approved=approved,
            note=note,
        )
        response = {"ok": True, "batch_id": batch.id, "status": batch.status, "replayed": False}
        self._record(session, idempotency_key=idempotency_key, command_type=self.REVIEW, actor_user_id=owner.id, batch_id=batch_id, request_hash=request_hash, response=response)
        return response

    def ingest_evidence(self, session: Session, *, actor_telegram_id: int, batch_id: int, idempotency_key: str) -> dict:
        owner = self._require_owner(session, actor_telegram_id)
        request_hash = self._fingerprint(self.INGEST, actor_telegram_id, batch_id, {})
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        ingested, skipped = HistoricalEvidenceIngestionService().ingest_reviewed_batch(
            session,
            batch_id=batch_id,
            reviewer_user_id=owner.id,
        )
        response = {"ok": True, "batch_id": batch_id, "ingested": ingested, "skipped": skipped, "replayed": False}
        self._record(session, idempotency_key=idempotency_key, command_type=self.INGEST, actor_user_id=owner.id, batch_id=batch_id, request_hash=request_hash, response=response)
        return response
