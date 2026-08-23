from sqlalchemy import select
from capitalguard.application.services.historical_content_understanding_service import HistoricalContentUnderstandingService
from capitalguard.application.services.historical_financial_candidate_service import HistoricalFinancialCandidateService
from capitalguard.application.services.historical_message_foundation_service import HistoricalMessageFoundationService
from capitalguard.infrastructure.db.models import HistoricalFinancialCandidate, HistoricalSignal, Recommendation, UserTrade
from tests.test_historical_evidence_ingestion_service import make_reviewed_batch

def test_g3_extracts_provenanced_candidates_without_financial_side_effects(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Entry 62000 SL 60000 TP1 63000 TP2 65000 Leverage 5x Risk 2%"
    receipt.content_hash = "f" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)
    candidates = HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id)
    fields = {candidate.field_type for candidate in candidates}
    assert {"ASSET", "DIRECTION", "ENTRY", "STOP_LOSS", "TARGET", "LEVERAGE", "RISK_PERCENT"}.issubset(fields)
    assert all(item.status == "CANDIDATE" and item.review_status == "PENDING" for item in candidates)
    assert all(item.provenance_json["revision_id"] == revision.id and item.span_text for item in candidates)
    assert HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id) == candidates
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []
