from sqlalchemy import select

from capitalguard.application.services.historical_content_understanding_service import HistoricalContentUnderstandingService
from capitalguard.application.services.historical_message_foundation_service import HistoricalMessageFoundationService
from capitalguard.infrastructure.db.models import HistoricalContentInterpretation, HistoricalSignal, Recommendation, UserTrade
from tests.test_historical_evidence_ingestion_service import make_reviewed_batch


def test_g2_is_deterministic_provenanced_and_non_financial(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "تحليل للذهب اليوم بعد خبر اقتصادي"
    receipt.content_hash = "d" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    service = HistoricalContentUnderstandingService()
    first = service.interpret_revision(db_session, revision_id=revision.id)
    second = service.interpret_revision(db_session, revision_id=revision.id)

    assert first.id == second.id
    assert first.content_type == "NEWS"
    assert first.provenance_json["revision_id"] == revision.id
    assert first.provenance_json["content_hash"] == revision.content_hash
    assert db_session.execute(select(HistoricalContentInterpretation)).scalars().all() == [first]
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []


def test_g2_ambiguous_content_fails_closed_to_unknown(db_session):
    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "BTC ربما يتحرك قريباً"
    receipt.content_hash = "e" * 64
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    result = HistoricalContentUnderstandingService().interpret_revision(db_session, revision_id=revision.id)

    assert result.content_type == "UNKNOWN"
    assert result.ambiguity_reason == "INSUFFICIENT_NON_FINANCIAL_CONTEXT"
