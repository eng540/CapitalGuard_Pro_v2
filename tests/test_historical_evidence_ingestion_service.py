from datetime import datetime, timezone

import pytest

from capitalguard.application.services.historical_evidence_ingestion_service import (
    HistoricalEvidenceIngestionError,
    HistoricalEvidenceIngestionService,
)
from capitalguard.application.services.historical_signal_service import HistoricalSignalService
from capitalguard.infrastructure.db.models import HistoricalForwardReceipt


def make_reviewed_batch(db_session):
    batch = HistoricalSignalService().create_import_batch(
        db_session,
        source_kind="TELEGRAM_FORWARD",
        manifest=[{"message_id": 1}],
        metadata={"owner_review": {"approved": True, "reviewer_user_id": 99}},
    )
    batch.accepted_records = 1
    batch.status = "VALIDATED"
    receipt = HistoricalForwardReceipt(
        batch_id=batch.id,
        forwarding_user_id=99,
        receiver_chat_id=500,
        receiver_message_id=1,
        source_chat_id=-100123,
        source_message_id=77,
        source_message_revision=0,
        source_origin_type="CHANNEL",
        source_message_timestamp=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        raw_text="#BTCUSDT LONG Entry 69000 Stop 68000 TP1 70000",
        content_hash="a" * 64,
        validation_status="STAGED",
        metadata_json={},
    )
    db_session.add(receipt)
    db_session.flush()
    return batch, receipt


def test_ingestion_requires_owner_approval(db_session):
    batch = HistoricalSignalService().create_import_batch(
        db_session,
        source_kind="TELEGRAM_FORWARD",
        manifest=[{"message_id": 1}],
    )
    with pytest.raises(HistoricalEvidenceIngestionError, match="approved owner review"):
        HistoricalEvidenceIngestionService().ingest_reviewed_batch(
            db_session,
            batch_id=batch.id,
            reviewer_user_id=99,
        )


def test_reviewed_receipts_become_evidence_and_are_idempotent(db_session):
    batch, receipt = make_reviewed_batch(db_session)
    service = HistoricalEvidenceIngestionService()
    ingested, skipped = service.ingest_reviewed_batch(
        db_session,
        batch_id=batch.id,
        reviewer_user_id=99,
    )
    assert (ingested, skipped) == (1, 0)
    assert receipt.validation_status == "INGESTED"
    assert receipt.evidence_id is not None
    evidence_id = receipt.evidence_id

    ingested_again, skipped_again = service.ingest_reviewed_batch(
        db_session,
        batch_id=batch.id,
        reviewer_user_id=99,
    )
    assert (ingested_again, skipped_again) == (0, 1)
    assert receipt.evidence_id == evidence_id
