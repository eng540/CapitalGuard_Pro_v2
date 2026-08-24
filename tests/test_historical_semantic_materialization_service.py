from datetime import datetime, timezone

from sqlalchemy import select

from capitalguard.application.services.historical_message_foundation_service import (
    HistoricalMessageFoundationService,
)
from capitalguard.application.services.historical_semantic_materialization_service import (
    HistoricalSemanticMaterializationService,
)
from capitalguard.infrastructure.db.models import HistoricalFinancialCandidate
from tests.test_historical_evidence_ingestion_service import make_reviewed_batch


def _revision(db_session, text: str, content_hash: str, metadata=None, batch=None, receiver_message_id=None):
    if batch is None:
        batch, receipt = make_reviewed_batch(db_session)
    else:
        from capitalguard.infrastructure.db.models import HistoricalForwardReceipt
        receipt = HistoricalForwardReceipt(
            batch_id=batch.id,
            forwarding_user_id=99,
            receiver_chat_id=500,
            receiver_message_id=receiver_message_id,
            source_chat_id=-100123,
            source_message_id=receiver_message_id,
            source_message_revision=0,
            source_origin_type="CHANNEL",
            source_message_timestamp=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            raw_text=text,
            content_hash=content_hash * 64,
            validation_status="STAGED",
            metadata_json=metadata or {},
        )
        db_session.add(receipt)
        db_session.flush()
    receipt.raw_text = text
    receipt.content_hash = content_hash * 64
    receipt.metadata_json = metadata or {}
    receipt.source_message_timestamp = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    return HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)


def _image_result(entry="77000", stop_loss="76000", target="78000", leverage="5", side="LONG"):
    return {
        "status": "success",
        "data": {
            "asset": "BTCUSDT",
            "market": "Futures",
            "side": side,
            "entry": entry,
            "stop_loss": stop_loss,
            "targets": [{"price": target, "close_percent": 100.0}],
            "leverage": leverage,
        },
    }


def test_text_contract_materializes_canonical_values_with_field_evidence(db_session):
    revision = _revision(
        db_session,
        "#BTCUSDT Futures LONG Entry 77K SL 76K TP 78K Leverage 5X",
        "a",
    )
    result = HistoricalSemanticMaterializationService().materialize_revision(
        db_session, revision_id=revision.id
    )

    assert result["status"] == "SUCCESS"
    assert result["canonical"] == {
        "asset": "BTCUSDT",
        "direction": "LONG",
        "entry": "77000",
        "stop_loss": "76000",
        "leverage": "5",
        "targets": ["78000"],
        "market": "FUTURES",
    }
    entry_evidence = result["field_evidence"]["entry"][0]
    assert entry_evidence["modality"] == "TEXT"
    assert "Entry 77K" in entry_evidence["span"]
    assert entry_evidence["normalization"]["normalized_value"] == "77000"
    assert entry_evidence["validation_status"] == "CANDIDATE"
    assert entry_evidence["final_semantic_status"] == "SUCCESS"


def test_missing_values_remain_incomplete_and_are_not_invented(db_session):
    revision = _revision(db_session, "#BTCUSDT LONG TP 78K", "b")
    result = HistoricalSemanticMaterializationService().materialize_revision(
        db_session, revision_id=revision.id
    )

    assert result["status"] == "INCOMPLETE"
    assert result["canonical"]["entry"] is None
    assert result["canonical"]["stop_loss"] is None
    assert set(("entry", "stop_loss")).issubset(set(result["missing_fields"]))


def test_image_candidates_are_materialized_with_media_provenance(db_session):
    revision = _revision(
        db_session,
        "",
        "c",
        metadata={"media": {"media_type": "PHOTO", "file_id": "photo-file-1", "media_unique_id": "photo-unique-1"}},
    )
    result = HistoricalSemanticMaterializationService().materialize_revision(
        db_session,
        revision_id=revision.id,
        image_result=_image_result(),
        image_provenance={
            "media_id": "photo-unique-1",
            "file_id": "photo-file-1",
            "provider": "vision-test",
            "model": "vision-contract-test",
        },
    )

    assert result["status"] == "SUCCESS"
    assert result["canonical"]["entry"] == "77000"
    assert result["canonical"]["leverage"] == "5"
    entry_evidence = result["field_evidence"]["entry"][0]
    assert entry_evidence["modality"] == "IMAGE"
    assert entry_evidence["provenance"]["media_id"] == "photo-unique-1"
    assert entry_evidence["normalization"]["normalized_value"] == "77000"
    assert entry_evidence["final_semantic_status"] == "SUCCESS"


def test_text_image_conflict_is_preserved_without_silent_winner(db_session):
    revision = _revision(db_session, "#BTCUSDT Futures LONG Entry 77K SL 76K TP 78K", "d")
    result = HistoricalSemanticMaterializationService().materialize_revision(
        db_session,
        revision_id=revision.id,
        image_result=_image_result(entry="78000"),
        image_provenance={"media_id": "photo-unique-2"},
    )

    assert result["status"] == "CONFLICT"
    assert result["canonical"]["entry"] is None
    assert "entry" in result["conflicting_fields"]
    assert {item["modality"] for item in result["field_evidence"]["entry"]} == {"TEXT", "IMAGE"}


def test_reprocessing_is_idempotent_for_same_revision_and_image(db_session):
    revision = _revision(db_session, "#BTCUSDT Futures LONG Entry 77K SL 76K TP 78K", "e")
    service = HistoricalSemanticMaterializationService()
    first = service.materialize_revision(
        db_session,
        revision_id=revision.id,
        image_result=_image_result(),
        image_provenance={"media_id": "photo-unique-3"},
    )
    first_count = db_session.execute(select(HistoricalFinancialCandidate)).scalars().all()
    second = service.materialize_revision(
        db_session,
        revision_id=revision.id,
        image_result=_image_result(),
        image_provenance={"media_id": "photo-unique-3"},
    )
    second_count = db_session.execute(select(HistoricalFinancialCandidate)).scalars().all()

    assert second == first
    assert len(second_count) == len(first_count)


def test_related_message_context_requires_approved_relationship_and_preserves_revisions(db_session):
    from decimal import Decimal
    from capitalguard.infrastructure.db.models import HistoricalMessageRelationship

    batch, _ = make_reviewed_batch(db_session)
    anchor = _revision(db_session, "#BTCUSDT Futures LONG", "f", batch=batch, receiver_message_id=101)
    values = _revision(db_session, "Entry 77K SL 76K TP 78K Leverage 5X", "g", batch=batch, receiver_message_id=102)
    foundation = HistoricalMessageFoundationService()
    relation = foundation.propose_relationship(
        db_session,
        source_message_id=values.message_id,
        target_message_id=anchor.message_id,
        relationship_type="POSSIBLE_UPDATE_OF",
        confidence=Decimal("0.9000"),
        evidence={"reply_to": True},
    )
    foundation.review_relationship(
        db_session,
        relationship_id=relation.id,
        reviewer_user_id=99,
        status="ACCEPTED",
    )

    result = HistoricalSemanticMaterializationService().materialize_related_revisions(
        db_session,
        anchor_revision_id=anchor.id,
        related_revision_ids=[values.id],
    )

    assert result["status"] == "SUCCESS"
    assert result["canonical"]["direction"] == "LONG"
    assert result["canonical"]["entry"] == "77000"
    assert result["related_context"]["source_revision_ids"] == [anchor.id, values.id]
    entry_evidence = result["field_evidence"]["entry"]
    assert entry_evidence[0]["provenance"]["revision_id"] == values.id
    assert db_session.execute(select(HistoricalMessageRelationship)).scalar_one().review_status == "ACCEPTED"


def test_semantic_materialization_stops_before_g5_signal_creation(db_session):
    from sqlalchemy import select
    from capitalguard.infrastructure.db.models import HistoricalSignal, HistoricalRecommendationDraft

    revision = _revision(
        db_session,
        "#BTCUSDT Futures LONG Entry 77K SL 76K TP 78K Leverage 5X",
        "h",
    )
    result = HistoricalSemanticMaterializationService().materialize_revision(
        db_session, revision_id=revision.id
    )

    assert result["status"] == "SUCCESS"
    assert db_session.execute(select(HistoricalRecommendationDraft)).scalar_one().evidence_chain_json["semantic_materialization"]["canonical"]["entry"] == "77000"
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []
