import pytest
from sqlalchemy import select

from capitalguard.application.services.historical_adjudication_service import HistoricalAdjudicationService
from capitalguard.application.services.historical_content_understanding_service import HistoricalContentUnderstandingService
from capitalguard.application.services.historical_evidence_ingestion_service import HistoricalEvidenceIngestionService
from capitalguard.application.services.historical_financial_candidate_service import HistoricalFinancialCandidateService
from capitalguard.application.services.historical_message_foundation_service import HistoricalMessageFoundationService
from capitalguard.application.services.historical_signal_materialization_service import (
    HistoricalSignalMaterializationBlocked,
    HistoricalSignalMaterializationService,
)
from capitalguard.infrastructure.db.models import (
    HistoricalMarketEvidence,
    HistoricalRecommendationDraft,
    HistoricalSignal,
    HistoricalSignalMaterialization,
    Recommendation,
    UserTrade,
)
from tests.test_historical_evidence_ingestion_service import make_reviewed_batch


def accepted_g5_draft(db_session):
    batch, receipt = make_reviewed_batch(db_session)
    HistoricalEvidenceIngestionService().ingest_reviewed_batch(db_session, batch_id=batch.id, reviewer_user_id=99)
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)
    for candidate in HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id):
        candidate.review_status = "ACCEPTED"
    adjudication = HistoricalAdjudicationService()
    draft = adjudication.adjudicate(db_session, revision_id=revision.id)
    adjudication.review(db_session, draft_id=draft.id, reviewer_user_id=99, decision="ACCEPTED", note="source chain reviewed")
    return draft, revision


def test_g5_materializes_accepted_draft_once_with_full_provenance(db_session):
    draft, revision = accepted_g5_draft(db_session)
    service = HistoricalSignalMaterializationService()

    signal = service.materialize(db_session, draft_id=draft.id)
    same_signal = service.materialize(db_session, draft_id=draft.id)
    bridge = db_session.execute(select(HistoricalSignalMaterialization)).scalar_one()

    assert same_signal.id == signal.id
    assert signal.status == "MATERIALIZED"
    assert signal.evidence_id == revision.evidence_id
    assert bridge.draft_id == draft.id
    assert bridge.revision_id == revision.id
    assert bridge.provenance_json["canonical_message_id"] == revision.message_id
    assert bridge.provenance_json["evidence_id"] == revision.evidence_id
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == [signal]
    assert db_session.execute(select(HistoricalMarketEvidence)).scalars().all() == []
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []


def test_g5_blocks_unaccepted_and_incomplete_drafts_without_signal(db_session):
    draft, revision = accepted_g5_draft(db_session)
    draft.status = "REJECTED"
    with pytest.raises(HistoricalSignalMaterializationBlocked, match="DRAFT_NOT_ACCEPTED"):
        HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []

    draft.status = "ACCEPTED"
    revision.evidence_id = None
    with pytest.raises(HistoricalSignalMaterializationBlocked, match="PROVENANCE_INCOMPLETE"):
        HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []


def test_g5_uses_proven_source_timestamp_not_review_or_runtime_time(db_session):
    draft, revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)

    assert signal.decision_timestamp.replace(tzinfo=revision.source_timestamp.tzinfo) == revision.source_timestamp
    assert signal.decision_timestamp <= draft.reviewed_at
