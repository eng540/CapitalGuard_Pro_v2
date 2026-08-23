from sqlalchemy import select
from capitalguard.application.services.historical_message_foundation_service import HistoricalMessageFoundationService
from capitalguard.application.services.historical_content_understanding_service import HistoricalContentUnderstandingService
from capitalguard.application.services.historical_financial_candidate_service import HistoricalFinancialCandidateService
from capitalguard.application.services.historical_adjudication_service import HistoricalAdjudicationService
from capitalguard.infrastructure.db.models import HistoricalRecommendationDraft, HistoricalSignal, Recommendation, UserTrade
from tests.test_historical_evidence_ingestion_service import make_reviewed_batch

def test_g4_creates_reviewable_draft_only_from_accepted_candidates(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Entry 62000 SL 60000 TP1 65000"
    receipt.content_hash = "k" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)
    candidates = HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id)
    for item in candidates: item.review_status = "ACCEPTED"
    service = HistoricalAdjudicationService()
    draft = service.adjudicate(db_session, revision_id=revision.id)
    assert draft.status == "DRAFT" and draft.draft_kind == "NEW_RECOMMENDATION"
    assert service.adjudicate(db_session, revision_id=revision.id).id == draft.id
    assert service.review(db_session, draft_id=draft.id, reviewer_user_id=99, decision="ACCEPTED", note="evidence reviewed").status == "ACCEPTED"
    assert db_session.execute(select(HistoricalRecommendationDraft)).scalar_one().evidence_chain_json
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []

def test_g4_requires_review_for_unaccepted_or_conflicting_candidates(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Entry 62000"
    receipt.content_hash = "l" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)
    HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id)
    draft = HistoricalAdjudicationService().adjudicate(db_session, revision_id=revision.id)
    assert draft.status == "REVIEW_REQUIRED"


def test_g4_reviewer_rejection_and_override_preserve_audit_metadata(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Entry 62000"
    receipt.content_hash = "q" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)
    for candidate in HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id):
        candidate.review_status = "ACCEPTED"
    service = HistoricalAdjudicationService()
    draft = service.adjudicate(db_session, revision_id=revision.id)
    rejected = service.review(db_session, draft_id=draft.id, reviewer_user_id=99, decision="REJECTED", note="analysis only", override={"entry": "62500"})
    assert rejected.status == "REJECTED"
    assert rejected.review_note == "analysis only"
    assert rejected.override_json == {"entry": "62500"}
    assert rejected.evidence_chain_json["ENTRY"]
