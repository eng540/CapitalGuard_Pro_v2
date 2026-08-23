from capitalguard.application.services.historical_adjudication_service import HistoricalAdjudicationService
from capitalguard.application.services.historical_content_understanding_service import HistoricalContentUnderstandingService
from capitalguard.application.services.historical_financial_candidate_service import HistoricalFinancialCandidateService
from capitalguard.application.services.historical_message_foundation_service import HistoricalMessageFoundationService
from tests.test_historical_evidence_ingestion_service import make_reviewed_batch


def test_g4_lifecycle_fails_closed_without_accepted_message_relationship(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Entry 62000"
    receipt.content_hash = "m" * 64
    first_revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    first_interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=first_revision.id)
    first_candidates = HistoricalFinancialCandidateService().extract(db_session, interpretation_id=first_interpretation.id)
    for candidate in first_candidates:
        candidate.review_status = "ACCEPTED"
    service = HistoricalAdjudicationService()
    parent = service.adjudicate(db_session, revision_id=first_revision.id)
    service.review(db_session, draft_id=parent.id, reviewer_user_id=99, decision="ACCEPTED")

    receipt.receiver_message_id = 88
    receipt.source_message_id = 88
    receipt.source_message_revision = 1
    receipt.raw_text = "Move SL to 61000"
    receipt.content_hash = "n" * 64
    db_session.add(receipt)
    db_session.flush()
    update_revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    update_interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=update_revision.id)
    update_candidates = HistoricalFinancialCandidateService().extract(db_session, interpretation_id=update_interpretation.id)
    for candidate in update_candidates:
        candidate.review_status = "ACCEPTED"
    draft = service.adjudicate_lifecycle(db_session, revision_id=update_revision.id, related_draft_id=parent.id)
    assert draft.status == "REVIEW_REQUIRED"


def test_g4_e2e_reviewed_update_remains_historical_draft(db_session):
    from datetime import datetime, timezone
    from decimal import Decimal
    from capitalguard.infrastructure.db.models import HistoricalForwardReceipt, HistoricalMessageRelationship, HistoricalSignal, Recommendation, UserTrade
    from sqlalchemy import select
    batch, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Entry 62000"
    receipt.content_hash = "o" * 64
    foundation = HistoricalMessageFoundationService()
    first = foundation.record_receipt(db_session, receipt=receipt)
    first_i = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=first.id)
    for candidate in HistoricalFinancialCandidateService().extract(db_session, interpretation_id=first_i.id): candidate.review_status = "ACCEPTED"
    service = HistoricalAdjudicationService(); parent = service.adjudicate(db_session, revision_id=first.id)
    service.review(db_session, draft_id=parent.id, reviewer_user_id=99, decision="ACCEPTED")
    update_receipt = HistoricalForwardReceipt(batch_id=batch.id, forwarding_user_id=99, receiver_chat_id=500, receiver_message_id=998, source_chat_id=-100123, source_message_id=998, source_message_revision=0, source_origin_type="CHANNEL", source_message_timestamp=datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc), source_reply_to_message_id=receipt.source_message_id, raw_text="Move SL to 61000", content_hash="p" * 64, validation_status="STAGED", metadata_json={})
    db_session.add(update_receipt); db_session.flush()
    update = foundation.record_receipt(db_session, receipt=update_receipt)
    relation = foundation.propose_relationship(db_session, source_message_id=update.message_id, target_message_id=first.message_id, relationship_type="POSSIBLE_UPDATE_OF", confidence=Decimal("0.90"), evidence={"reply_to": True})
    foundation.review_relationship(db_session, relationship_id=relation.id, reviewer_user_id=99, status="ACCEPTED", note="reply evidence")
    update_i = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=update.id)
    for candidate in HistoricalFinancialCandidateService().extract(db_session, interpretation_id=update_i.id): candidate.review_status = "ACCEPTED"
    draft = service.adjudicate_lifecycle(db_session, revision_id=update.id, related_draft_id=parent.id)
    assert draft.status == "DRAFT" and draft.draft_kind == "SL_UPDATE", draft.adjudication_reason
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []


def test_g4_lifecycle_kind_priority_is_deterministic():
    text_cases = {
        "CANCEL": "Cancel this setup",
        "CLOSE": "Trade closed",
        "TARGET_REACHED": "TP hit target reached",
        "PARTIAL_EXIT": "Partial exit taken",
    }
    assert set(text_cases) == {"CANCEL", "CLOSE", "TARGET_REACHED", "PARTIAL_EXIT"}


def test_g4_e2e_reviewed_close_cancel_target_and_partial_remain_historical_drafts(db_session):
    from datetime import datetime, timezone
    from decimal import Decimal
    from sqlalchemy import select
    from capitalguard.infrastructure.db.models import HistoricalForwardReceipt, HistoricalSignal, Recommendation, UserTrade

    batch, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Entry 62000"
    receipt.content_hash = "r" * 64
    foundation = HistoricalMessageFoundationService()
    first = foundation.record_receipt(db_session, receipt=receipt)
    first_i = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=first.id)
    for candidate in HistoricalFinancialCandidateService().extract(db_session, interpretation_id=first_i.id):
        candidate.review_status = "ACCEPTED"
    service = HistoricalAdjudicationService()
    parent = service.adjudicate(db_session, revision_id=first.id)
    service.review(db_session, draft_id=parent.id, reviewer_user_id=99, decision="ACCEPTED")

    scenarios = [
        (901, "Trade closed", "CLOSE"),
        (902, "Cancel this setup", "CANCEL"),
        (903, "Partial exit taken", "PARTIAL_EXIT"),
        (904, "TP hit target reached", "TARGET_REACHED"),
    ]

    drafts = []
    for message_id, raw_text, expected_kind in scenarios:
        update_receipt = HistoricalForwardReceipt(
            batch_id=batch.id,
            forwarding_user_id=99,
            receiver_chat_id=500,
            receiver_message_id=message_id + 1000,
            source_chat_id=-100123,
            source_message_id=message_id,
            source_message_revision=0,
            source_origin_type="CHANNEL",
            source_message_timestamp=datetime(2026, 8, 20, 13, message_id % 60, tzinfo=timezone.utc),
            source_reply_to_message_id=receipt.source_message_id,
            raw_text=raw_text,
            content_hash=(str(message_id) * 64)[:64],
            validation_status="STAGED",
            metadata_json={},
        )
        db_session.add(update_receipt)
        db_session.flush()
        update = foundation.record_receipt(db_session, receipt=update_receipt)
        relation = foundation.propose_relationship(
            db_session,
            source_message_id=update.message_id,
            target_message_id=first.message_id,
            relationship_type="POSSIBLE_EVENT_OF",
            confidence=Decimal("0.90"),
            evidence={"reply_to": True},
        )
        foundation.review_relationship(db_session, relationship_id=relation.id, reviewer_user_id=99, status="ACCEPTED", note="reply evidence")
        update_i = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=update.id)
        HistoricalFinancialCandidateService().extract(db_session, interpretation_id=update_i.id)
        draft = service.adjudicate_lifecycle(db_session, revision_id=update.id, related_draft_id=parent.id)
        assert draft.status == "DRAFT"
        assert draft.draft_kind == expected_kind
        drafts.append(draft.id)

    assert len(set(drafts)) == 4
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []
