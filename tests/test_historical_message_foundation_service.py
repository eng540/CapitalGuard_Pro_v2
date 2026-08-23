from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from capitalguard.application.services.historical_message_foundation_service import HistoricalMessageFoundationService
from capitalguard.infrastructure.db.models import HistoricalForwardReceipt, HistoricalMessageRelationship, HistoricalMessageRevision, HistoricalSignal, Recommendation, UserTrade
from tests.test_historical_evidence_ingestion_service import make_reviewed_batch


def test_foundation_preserves_canonical_identity_and_immutable_revisions(db_session):
    batch, receipt = make_reviewed_batch(db_session)
    service = HistoricalMessageFoundationService()

    first = service.record_receipt(db_session, receipt=receipt)
    assert first.revision_number == 1
    assert first.safe_classification == "POSSIBLE_RECOMMENDATION"
    assert service.record_receipt(db_session, receipt=receipt).id == first.id

    updated = HistoricalForwardReceipt(
        batch_id=batch.id,
        forwarding_user_id=99,
        receiver_chat_id=500,
        receiver_message_id=2,
        source_chat_id=receipt.source_chat_id,
        source_message_id=receipt.source_message_id,
        source_message_revision=1,
        source_origin_type="CHANNEL",
        source_message_timestamp=datetime(2026, 8, 20, 12, 5, tzinfo=timezone.utc),
        raw_text="#BTCUSDT LONG Entry 69500 Stop 68000 TP1 70000",
        content_hash="b" * 64,
        validation_status="STAGED",
        metadata_json={},
    )
    db_session.add(updated)
    db_session.flush()
    second = service.record_receipt(db_session, receipt=updated)

    assert second.message_id == first.message_id
    assert second.revision_number == 2
    assert db_session.execute(select(HistoricalMessageRevision).where(HistoricalMessageRevision.message_id == first.message_id)).scalars().all() == [first, second]


def test_relationship_is_reviewable_and_idempotent(db_session):
    batch, receipt = make_reviewed_batch(db_session)
    service = HistoricalMessageFoundationService()
    source = service.record_receipt(db_session, receipt=receipt)
    target_receipt = HistoricalForwardReceipt(
        batch_id=batch.id,
        forwarding_user_id=99,
        receiver_chat_id=500,
        receiver_message_id=3,
        source_chat_id=-100123,
        source_message_id=78,
        source_message_revision=0,
        source_origin_type="CHANNEL",
        source_message_timestamp=datetime(2026, 8, 20, 12, 4, tzinfo=timezone.utc),
        source_reply_to_message_id=77,
        raw_text="Move SL to 68000",
        content_hash="c" * 64,
        validation_status="STAGED",
        metadata_json={},
    )
    db_session.add(target_receipt)
    db_session.flush()
    target = service.record_receipt(db_session, receipt=target_receipt)

    relation = service.propose_relationship(db_session, source_message_id=target.message_id, target_message_id=source.message_id, relationship_type="POSSIBLE_UPDATE_OF", confidence=Decimal("0.8200"), evidence={"reply_to": True, "same_channel": True})
    assert relation.review_status == "PENDING"
    assert service.propose_relationship(db_session, source_message_id=target.message_id, target_message_id=source.message_id, relationship_type="POSSIBLE_UPDATE_OF", confidence=Decimal("0.8200"), evidence={"reply_to": True}).id == relation.id
    reviewed = service.review_relationship(db_session, relationship_id=relation.id, reviewer_user_id=99, status="ACCEPTED", note="reply proof")
    assert reviewed.review_status == "ACCEPTED"
    assert db_session.execute(select(HistoricalMessageRelationship)).scalar_one().review_note == "reply proof"


def test_g1_e2e_builds_message_memory_and_reviewable_links_without_financial_side_effects(db_session):
    batch, _ = make_reviewed_batch(db_session)
    service = HistoricalMessageFoundationService()

    def receipt(message_id, text, content_hash, reply_to=None):
        item = HistoricalForwardReceipt(
            batch_id=batch.id,
            forwarding_user_id=99,
            receiver_chat_id=500,
            receiver_message_id=1000 + message_id,
            source_chat_id=-100123,
            source_message_id=message_id,
            source_message_revision=0,
            source_origin_type="CHANNEL",
            source_message_timestamp=datetime(2026, 8, 20, 12, message_id, tzinfo=timezone.utc),
            source_reply_to_message_id=reply_to,
            raw_text=text,
            content_hash=content_hash * 64,
            validation_status="STAGED",
            metadata_json={},
        )
        db_session.add(item)
        db_session.flush()
        return service.record_receipt(db_session, receipt=item)

    a = receipt(10, "BTC LONG 62000", "a")
    b = receipt(11, "Move SL to 61000", "b", reply_to=10)
    c = receipt(12, "TP1 hit", "c", reply_to=10)
    d = receipt(13, "BTC market commentary", "d")

    service.propose_relationship(db_session, source_message_id=b.message_id, target_message_id=a.message_id, relationship_type="POSSIBLE_UPDATE_OF", confidence=Decimal("0.82"), evidence={"reply_to": True, "same_channel": True})
    service.propose_relationship(db_session, source_message_id=c.message_id, target_message_id=a.message_id, relationship_type="POSSIBLE_EVENT_OF", confidence=Decimal("0.82"), evidence={"reply_to": True, "same_channel": True})

    assert [item.safe_classification for item in (a, b, c, d)] == ["POSSIBLE_RECOMMENDATION", "REPLY", "REPLY", "TEXT"]
    assert db_session.execute(select(HistoricalMessageRelationship)).scalars().all().__len__() == 2
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []
