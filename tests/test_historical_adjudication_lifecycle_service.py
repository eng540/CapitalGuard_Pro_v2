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
