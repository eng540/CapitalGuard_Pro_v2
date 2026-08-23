from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalForwardReceipt, HistoricalImportBatch, HistoricalSignal
from capitalguard.infrastructure.db.repository import ParsingRepository

from .historical_parser_service import HistoricalParserService
from .historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError
from .parsing_service import ParsingService


class HistoricalEvidenceIngestionError(ValueError):
    """Raised when a staged historical batch cannot be ingested safely."""


class HistoricalEvidenceIngestionService:
    """Moves reviewed forwarding receipts into immutable evidence, never live entities."""

    def __init__(self, signal_service: HistoricalSignalService | None = None, parser: HistoricalParserService | None = None):
        self.signal_service = signal_service or HistoricalSignalService()
        self.parser = parser or HistoricalParserService(ParsingService(ParsingRepository))

    def _ensure_replayable_signal(self, session: Session, *, receipt: HistoricalForwardReceipt) -> bool:
        """Create exactly one historical-only signal when immutable evidence parses fully."""
        if not receipt.evidence_id or receipt.source_message_timestamp is None:
            return False
        existing = session.execute(
            select(HistoricalSignal).where(HistoricalSignal.evidence_id == receipt.evidence_id)
        ).scalar_one_or_none()
        if existing is not None:
            return False
        parsed = self.parser.parse(receipt.raw_text or "")
        receipt.metadata_json = {
            **(receipt.metadata_json or {}),
            "historical_parse_status": parsed.parse_status,
            "historical_parse_errors": list(parsed.errors),
        }
        if parsed.parse_status != "PARSED":
            return False
        data = parsed.data or {}
        self.signal_service.create_signal(
            session,
            evidence_id=receipt.evidence_id,
            decision_timestamp=receipt.source_message_timestamp,
            channel_catalog_id=receipt.batch.channel_catalog_id if receipt.batch else None,
            asset=data.get("asset"),
            side=data.get("side"),
            entry=data.get("entry"),
            stop_loss=data.get("stop_loss"),
            targets=data.get("targets"),
            market=data.get("market"),
        )
        return True

    def ensure_replayable_signals(self, session: Session, *, batch_id: int) -> int:
        """Backfill only historical parsed signals for an already-ingested batch.

        This is idempotent and repairs batches created before signal construction
        was made part of Evidence Ingestion; it never creates live entities.
        """
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None or batch.status != "EVIDENCE_INGESTED":
            raise HistoricalEvidenceIngestionError("Batch requires evidence ingestion before replay preparation")
        receipts = session.execute(
            select(HistoricalForwardReceipt).where(
                HistoricalForwardReceipt.batch_id == batch_id,
                HistoricalForwardReceipt.validation_status == "INGESTED",
            )
        ).scalars().all()
        created = sum(1 for receipt in receipts if self._ensure_replayable_signal(session, receipt=receipt))
        session.flush()
        return created

    def ingest_reviewed_batch(
        self,
        session: Session,
        *,
        batch_id: int,
        reviewer_user_id: int,
    ) -> tuple[int, int]:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalEvidenceIngestionError("Historical batch does not exist")
        owner_review = (batch.metadata_json or {}).get("owner_review") or {}
        if batch.status == "EVIDENCE_INGESTED":
            created = self.ensure_replayable_signals(session, batch_id=batch_id)
            existing_receipts = session.execute(select(HistoricalForwardReceipt).where(HistoricalForwardReceipt.batch_id == batch_id)).scalars().all()
            return created, sum(1 for receipt in existing_receipts if receipt.validation_status == "INGESTED")
        if batch.status != "VALIDATED" or owner_review.get("approved") is not True:
            raise HistoricalEvidenceIngestionError("Batch requires approved owner review before evidence ingestion")
        if not reviewer_user_id:
            raise HistoricalEvidenceIngestionError("reviewer_user_id is required")
        receipts = session.execute(
            select(HistoricalForwardReceipt)
            .where(HistoricalForwardReceipt.batch_id == batch_id)
            .order_by(HistoricalForwardReceipt.id)
        ).scalars().all()
        ingested = 0
        skipped = 0
        for receipt in receipts:
            if receipt.validation_status != "STAGED":
                skipped += 1
                continue
            if receipt.source_message_timestamp is None:
                skipped += 1
                continue
            try:
                evidence = self.signal_service.ingest_evidence(
                    session,
                    source_kind="TELEGRAM_FORWARD",
                    batch_id=batch.id,
                    channel_catalog_id=batch.channel_catalog_id,
                    telegram_channel_id=receipt.source_chat_id,
                    telegram_message_id=receipt.source_message_id,
                    message_revision=receipt.source_message_revision or 0,
                    message_timestamp=receipt.source_message_timestamp,
                    raw_text=receipt.raw_text,
                    source_uri=f"telegram://{receipt.source_chat_id}/{receipt.source_message_id}",
                    ownership_proof_type="OWNER_REVIEW",
                    ownership_proof_ref=f"batch:{batch.id}:reviewer:{reviewer_user_id}",
                    metadata={
                        "receipt_id": receipt.id,
                        "source_edit_date": receipt.source_edit_date.isoformat() if receipt.source_edit_date else None,
                        "source_reply_to_message_id": receipt.source_reply_to_message_id,
                        "owner_reviewed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except HistoricalSignalValidationError as exc:
                raise HistoricalEvidenceIngestionError(str(exc)) from exc
            receipt.evidence_id = evidence.id
            receipt.validation_status = "INGESTED"
            receipt.metadata_json = {
                **(receipt.metadata_json or {}),
                "evidence_id": evidence.id,
                "ingested_by_user_id": reviewer_user_id,
            }
            self._ensure_replayable_signal(session, receipt=receipt)
            ingested += 1
        batch.metadata_json = {
            **(batch.metadata_json or {}),
            "evidence_ingestion": {
                "ingested": ingested,
                "skipped": skipped,
                "ingested_by_user_id": reviewer_user_id,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        batch.status = "EVIDENCE_INGESTED"
        session.flush()
        return ingested, skipped
