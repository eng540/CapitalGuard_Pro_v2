from datetime import datetime, timezone

import pytest

from capitalguard.application.services.historical_signal_service import (
    HistoricalSignalService,
    HistoricalSignalValidationError,
)


@pytest.fixture
def historical_service():
    return HistoricalSignalService()


def _timestamp(day: int, hour: int = 12):
    return datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc)


def test_evidence_import_is_idempotent_by_channel_message_revision(db_session, historical_service):
    first = historical_service.ingest_evidence(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        telegram_channel_id=-100123,
        telegram_message_id=77,
        message_revision=0,
        message_timestamp=_timestamp(1),
        raw_text="BTCUSDT LONG entry 60000",
    )
    second = historical_service.ingest_evidence(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        telegram_channel_id=-100123,
        telegram_message_id=77,
        message_revision=0,
        message_timestamp=_timestamp(1),
        raw_text="BTCUSDT LONG entry 60000",
    )
    assert first.id == second.id
    assert first.dedup_key == "telegram:-100123:77:r0"


def test_decision_and_market_replay_cannot_leak_future_data(db_session, historical_service):
    evidence = historical_service.ingest_evidence(
        db_session,
        source_kind="AUTHORIZED_USER_HISTORY",
        telegram_channel_id=-100123,
        telegram_message_id=78,
        message_timestamp=_timestamp(1),
        raw_text="BTCUSDT LONG",
    )
    signal = historical_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_timestamp(1, 12),
        asset="BTCUSDT",
        side="LONG",
    )

    with pytest.raises(HistoricalSignalValidationError, match="market_as_of"):
        historical_service.record_event(
            db_session,
            signal_id=signal.id,
            event_type="TP1",
            event_timestamp=_timestamp(2),
            market_as_of=_timestamp(3),
            data_source="TEST",
            price="61000",
            replay_status="VERIFIED",
            dedup_key="hist:78:tp1",
        )

    with pytest.raises(HistoricalSignalValidationError, match="event_timestamp"):
        historical_service.record_event(
            db_session,
            signal_id=signal.id,
            event_type="CREATED",
            event_timestamp=datetime(2025, 12, 31, tzinfo=timezone.utc),
            dedup_key="hist:78:created",
        )


def test_verified_replay_requires_point_in_time_evidence(db_session, historical_service):
    evidence = historical_service.ingest_evidence(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        message_timestamp=_timestamp(1),
        raw_text="ETHUSDT SHORT",
    )
    signal = historical_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_timestamp(1),
        asset="ETHUSDT",
        side="SHORT",
    )

    with pytest.raises(HistoricalSignalValidationError, match="VERIFIED replay"):
        historical_service.record_event(
            db_session,
            signal_id=signal.id,
            event_type="TP1",
            event_timestamp=_timestamp(2),
            replay_status="VERIFIED",
            dedup_key="hist:eth:tp1",
        )


def test_manual_history_stays_out_of_ranking_and_event_dedup_is_safe(db_session, historical_service):
    evidence = historical_service.ingest_evidence(
        db_session,
        source_kind="MANUAL_ADMIN_IMPORT",
        message_timestamp=_timestamp(1),
        raw_text="SOLUSDT LONG",
    )
    signal = historical_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_timestamp(1),
        asset="SOLUSDT",
        side="LONG",
    )
    event_one = historical_service.record_event(
        db_session,
        signal_id=signal.id,
        event_type="CREATED",
        event_timestamp=_timestamp(1),
        dedup_key="hist:sol:created",
    )
    event_two = historical_service.record_event(
        db_session,
        signal_id=signal.id,
        event_type="CREATED",
        event_timestamp=_timestamp(1),
        dedup_key="hist:sol:created",
    )
    assert event_one.id == event_two.id
    assert signal.trust_tier == "MANUAL_ATTESTED"
    assert signal.eligible_for_ranking is False


def test_import_batch_requires_validation_before_evidence_ingest(db_session, historical_service):
    batch = historical_service.create_import_batch(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        manifest=[{"message_id": 1}, {"message_id": 2}],
    )
    with pytest.raises(HistoricalSignalValidationError, match="VALIDATED batch"):
        historical_service.ingest_evidence(
            db_session,
            batch_id=batch.id,
            source_kind="TELEGRAM_EXPORT",
            telegram_message_id=1,
            message_timestamp=_timestamp(1),
            raw_text="BTCUSDT LONG",
        )

    historical_service.validate_import_batch(
        db_session,
        batch_id=batch.id,
        accepted_records=2,
        rejected_records=0,
    )
    evidence = historical_service.ingest_evidence(
        db_session,
        batch_id=batch.id,
        source_kind="TELEGRAM_EXPORT",
        telegram_message_id=1,
        message_timestamp=_timestamp(1),
        raw_text="BTCUSDT LONG",
    )
    assert batch.status == "VALIDATED"
    assert evidence.batch_id == batch.id


def test_historical_trader_follow_is_separate_from_live_trade(db_session, historical_service):
    from capitalguard.domain.entities import UserType
    from capitalguard.application.services.historical_signal_query_service import HistoricalSignalQueryService
    from capitalguard.infrastructure.db.repository import UserRepository

    trader = UserRepository(db_session).find_or_create(
        telegram_id=6201,
        user_type=UserType.TRADER,
        first_name="Historical Trader",
    )
    evidence = historical_service.ingest_evidence(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        message_timestamp=_timestamp(1),
        raw_text="BTCUSDT LONG",
    )
    signal = historical_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_timestamp(1),
        asset="BTCUSDT",
        side="LONG",
    )
    follow = historical_service.record_trader_follow(
        db_session,
        signal_id=signal.id,
        trader_user_id=trader.id,
        dedup_key="historical-follow:6201:signal-1",
    )
    results = HistoricalSignalQueryService().search(db_session, trader_user_id=trader.id)

    assert follow.attribution_kind == "TRADER_FOLLOW"
    assert [item.id for item in results] == [signal.id]


def test_historical_reputation_summary_separates_confidence(db_session, historical_service):
    from capitalguard.application.services.historical_reputation_service import HistoricalReputationService

    evidence = historical_service.ingest_evidence(
        db_session,
        source_kind="MANUAL_ADMIN_IMPORT",
        message_timestamp=_timestamp(1),
        raw_text="BTCUSDT LONG",
    )
    signal = historical_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_timestamp(1),
        analyst_id=123,
        asset="BTCUSDT",
        side="LONG",
    )
    signal.eligible_for_ranking = True
    summary = HistoricalReputationService.summarize(db_session, analyst_id=123)

    assert summary.total_signals == 1
    assert summary.verified_signals == 0
    assert summary.rank_eligible_signals == 0
    assert summary.excluded_signals == 1
    assert summary.confidence_weighted_sample == 0


def test_historical_attribution_review_is_auditable(db_session, historical_service):
    evidence = historical_service.ingest_evidence(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        message_timestamp=_timestamp(1),
        raw_text="#BTCUSDT LONG",
    )
    signal = historical_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_timestamp(1),
        asset="BTCUSDT",
        side="LONG",
    )
    attribution = historical_service.add_attribution(
        db_session,
        signal_id=signal.id,
        attribution_kind="CHANNEL",
        channel_id=7,
        proof_type="CHANNEL_ADMIN_CONFIRMATION",
        dedup_key="review:channel:7:signal:1",
    )
    reviewed = historical_service.review_attribution(
        db_session,
        attribution_id=attribution.id,
        reviewer_user_id=900,
        status="VERIFIED",
        note="Channel ownership evidence reviewed",
    )

    assert reviewed.status == "VERIFIED"
    assert reviewed.reviewed_by_user_id == 900
    assert reviewed.review_note == "Channel ownership evidence reviewed"
    assert reviewed.reviewed_at is not None
