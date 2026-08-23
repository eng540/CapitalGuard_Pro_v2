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


def test_g3_marks_multiple_entry_candidates_for_review(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Entry 62000 Entry 62500 SL 60000"
    receipt.content_hash = "g" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)
    candidates = HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id)
    entries = [item for item in candidates if item.field_type == "ENTRY"]
    assert len(entries) == 2
    assert {(item.status, item.review_status) for item in entries} == {("CONFLICT", "REVIEW_REQUIRED")}


def test_g3_extracts_timeframe_and_condition_as_candidates(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG 4h If BTC closes above 62000"
    receipt.content_hash = "h" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)
    candidates = HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id)
    assert {item.field_type for item in candidates}.issuperset({"ASSET", "DIRECTION", "TIMEFRAME", "CONDITION"})
    assert all(item.status == "CANDIDATE" for item in candidates)


def test_g3_extracts_percentage_currency_and_strategy_as_candidates(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "#BTCUSDT LONG Risk 2% Strategy: breakout retest USD"
    receipt.content_hash = "i" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    interpretation = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)
    candidates = HistoricalFinancialCandidateService().extract(db_session, interpretation_id=interpretation.id)
    assert {item.field_type for item in candidates}.issuperset({"PERCENTAGE", "CURRENCY", "STRATEGY"})
    assert all(item.review_status == "PENDING" for item in candidates)
