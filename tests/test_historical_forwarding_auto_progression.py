from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from capitalguard.application.services.historical_forwarding_service import HistoricalForwardingService
from capitalguard.application.services.historical_message_foundation_service import HistoricalMessageFoundationService
from capitalguard.application.services.historical_semantic_materialization_service import HistoricalSemanticMaterializationService
from capitalguard.application.services.historical_signal_service import HistoricalSignalService
from capitalguard.application.services.historical_market_replay_service import MarketCandle
from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    HistoricalForwardReceipt,
    HistoricalRecommendationDraft,
    HistoricalReplayRun,
    HistoricalSignal,
    HistoricalSignalEvidence,
    PublicationDelivery,
    Recommendation,
    UserTrade,
)


SOURCE_TIME = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, candles=None, error=None):
        self.candles = list(candles or [])
        self.error = error
        self.calls = 0

    def fetch(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.candles), "fake://historical-ohlcv"


def _candles(*, high=101, low=99):
    return [
        MarketCandle(
            asset="BTCUSDT",
            market="Futures",
            open_time=SOURCE_TIME + timedelta(minutes=5 * index),
            open=100,
            high=high,
            low=low,
            close=100,
            volume=1,
            data_source="FAKE",
        )
        for index in range(3)
    ]


def _auto_batch(db_session, *, raw_text, claim_status="CANONICAL", source_time=SOURCE_TIME):
    catalog = ChannelCatalog(
        telegram_channel_id=-1007001,
        channel_code="AUTO-PROGRESSION",
        public_ref="AUTO-PROGRESSION",
        title="Canonical historical source",
        is_active=True,
    )
    db_session.add(catalog)
    db_session.flush()
    batch = HistoricalSignalService().create_import_batch(
        db_session,
        source_kind="TELEGRAM_FORWARD",
        manifest=[{"message_id": 7001}],
        channel_catalog_id=catalog.id,
        metadata={
            "mode": "AUTO",
            "source_chat_id": catalog.telegram_channel_id,
            "claim_status": claim_status,
            "canonical_channel_catalog_id": catalog.id,
            "discovery_source": "DIRECT_FORWARD",
        },
    )
    batch.status = "STAGING"
    batch.total_records = 1
    batch.accepted_records = 1
    receipt = HistoricalForwardReceipt(
        batch_id=batch.id,
        forwarding_user_id=77,
        receiver_chat_id=700,
        receiver_message_id=7001,
        source_chat_id=catalog.telegram_channel_id,
        source_message_id=7001,
        source_message_revision=0,
        source_origin_type="CHANNEL",
        source_message_timestamp=source_time,
        raw_text=raw_text,
        content_hash="b" * 64,
        validation_status="STAGED",
        metadata_json={"event_kind": "INITIAL_SIGNAL"},
    )
    db_session.add(receipt)
    db_session.flush()
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    projection = HistoricalSemanticMaterializationService().materialize_revision(
        db_session,
        revision_id=revision.id,
    )
    db_session.flush()
    return batch, receipt, revision, projection


def test_auto_progression_recovers_reused_revision_without_receipt_binding(db_session):
    batch, receipt, revision, projection = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )
    # Reproduce G1 content-hash reuse: the canonical revision belongs to an
    # earlier receipt, so the strict receipt_id lookup cannot find it.
    revision.receipt_id = receipt.id + 99999
    db_session.flush()

    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles(high=111)),
    )

    assert projection["status"] == "SUCCESS"
    assert result["progressed"] == 1
    assert result["items"][0]["replay_status"] == "COMPLETED_UNVERIFIABLE"


def test_canonical_complete_auto_progression_materializes_and_replays_without_live_entities(db_session):
    batch, receipt, revision, projection = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )
    assert projection["status"] == "SUCCESS"
    preview = HistoricalForwardingService().preview_batch(db_session, batch_id=batch.id)
    assert preview.manifest["records"][0]["metadata"]["source_message_timestamp"] == SOURCE_TIME.isoformat()
    provider = FakeProvider(_candles(high=111))
    service = HistoricalForwardingService()

    result = service.auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=provider,
    )

    assert result["progressed"] == 1
    assert result["failed"] == 0
    assert result["review_required"] == 0
    assert result["status"] == "COMPLETED_UNVERIFIABLE"
    item = result["items"][0]
    assert item["status"] == "REPLAYED"
    assert item["replay_status"] == "COMPLETED_UNVERIFIABLE"
    assert item["lifecycle_status"] == "CLOSED_TARGETS"
    assert item["source_timestamp"] == SOURCE_TIME.isoformat()
    assert provider.calls == 1
    assert batch.status == "EVIDENCE_INGESTED"
    assert receipt.validation_status == "INGESTED"
    assert receipt.evidence_id is not None
    assert revision.evidence_id == receipt.evidence_id
    assert len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1
    assert len(db_session.execute(select(HistoricalReplayRun)).scalars().all()) == 1
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []
    assert db_session.execute(select(PublicationDelivery)).scalars().all() == []


def test_unclaimed_source_is_replayable_without_becoming_trusted_or_live(db_session):
    batch, receipt, _, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
        claim_status="UNCLAIMED",
    )
    provider = FakeProvider(_candles())

    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=provider,
    )

    assert result["status"] == "COMPLETED_UNVERIFIABLE"
    assert result["progressed"] == 1
    assert receipt.validation_status == "INGESTED"
    assert provider.calls == 1
    evidence = db_session.execute(select(HistoricalSignalEvidence)).scalars().all()
    assert len(evidence) == 1
    assert evidence[0].metadata_json["source_trust"] == "UNVERIFIED_FORWARD"
    signals = db_session.execute(select(HistoricalSignal)).scalars().all()
    assert len(signals) == 1
    assert signals[0].trust_tier == "UNVERIFIED"
    assert signals[0].eligible_for_ranking is False


def test_incomplete_semantic_projection_stays_review_required(db_session):
    batch, receipt, _, projection = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100",
    )
    assert projection["status"] == "INCOMPLETE"

    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles()),
    )

    assert result["progressed"] == 0
    assert result["review_required"] == 1
    assert result["items"][0]["status"] == "REVIEW_REQUIRED"
    assert receipt.validation_status == "STAGED"
    assert db_session.execute(select(HistoricalSignal)).scalars().all() == []


def test_provider_failure_preserves_evidence_and_g5_and_is_safe_to_retry(db_session):
    batch, receipt, _, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )
    service = HistoricalForwardingService()
    failed_provider = FakeProvider(error=RuntimeError("provider timeout"))

    first = service.auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=failed_provider,
    )

    assert first["progressed"] == 1
    assert first["failed"] == 1
    assert first["items"][0]["status"] == "REPLAY_FAILED"
    assert "G5 evidence was preserved" in first["items"][0]["reason"]
    assert receipt.validation_status == "INGESTED"
    assert len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1
    assert db_session.execute(select(HistoricalReplayRun)).scalars().all() == []

    second = service.auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles()),
    )
    assert second["items"][0]["status"] == "REPLAY_FAILED"
    assert second["failed"] == 1


def test_repeated_successful_job_is_idempotent(db_session):
    batch, _, _, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )
    service = HistoricalForwardingService()
    provider = FakeProvider(_candles())
    kwargs = {
        "batch_id": batch.id,
        "replay_end": SOURCE_TIME + timedelta(minutes=10),
        "limit": 3,
        "provider": provider,
    }

    first = service.auto_progress_canonical_batch(db_session, **kwargs)
    second = service.auto_progress_canonical_batch(db_session, **kwargs)

    assert first["status"] == second["status"] == "COMPLETED_UNVERIFIABLE"
    assert second["progressed"] == first["progressed"] == 1
    assert provider.calls == 1
    assert len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1
    assert len(db_session.execute(select(HistoricalReplayRun)).scalars().all()) == 1


def test_multiple_targets_are_preserved_and_partial_target_does_not_close_lifecycle(db_session):
    batch, _, _, projection = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 105@50% TP2 110@50% Futures",
    )
    assert projection["status"] == "SUCCESS"
    candles = _candles(high=106)
    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(candles),
    )

    assert result["progressed"] == 1
    signal = db_session.execute(select(HistoricalSignal)).scalar_one()
    assert len(signal.targets) == 2
    assert result["items"][0]["lifecycle_status"] == "ACTIVE"


def test_ambiguous_candle_is_unverifiable_and_not_final(db_session):
    batch, _, _, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )
    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles(high=110, low=90)),
    )

    item = result["items"][0]
    assert item["replay_status"] == "COMPLETED_UNVERIFIABLE"
    assert item["lifecycle_status"] == "AMBIGUOUS"
    assert item["status"] == "REPLAYED"
    assert result["status"] == "COMPLETED_UNVERIFIABLE"


def test_replay_window_shortfall_is_explicit_and_reaches_canonical_g6(db_session):
    old_time = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    batch, _, _, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
        source_time=old_time,
    )
    provider = FakeProvider([
        MarketCandle(
            asset="BTCUSDT", market="Futures", open_time=old_time,
            open=100, high=101, low=99, close=100, volume=1, data_source="FAKE",
        ),
        MarketCandle(
            asset="BTCUSDT", market="Futures", open_time=old_time + timedelta(minutes=15),
            open=100, high=101, low=99, close=100, volume=1, data_source="FAKE",
        ),
    ])

    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=old_time + timedelta(days=9),
        limit=2,
        provider=provider,
    )

    item = result["items"][0]
    assert result["progressed"] == 1
    assert item["status"] == "REPLAY_PARTIAL"
    assert item["replay_status"] == "PARTIAL_WINDOW"
    assert provider.calls == 1
    runs = db_session.execute(select(HistoricalReplayRun)).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "REPLAY_PARTIAL"
    assert runs[0].coverage_status == "PARTIAL_WINDOW"
    assert len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1


def test_mixed_batch_progresses_eligible_item_and_keeps_incomplete_item_staged(db_session):
    batch, _, _, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )
    second = HistoricalForwardReceipt(
        batch_id=batch.id,
        forwarding_user_id=77,
        receiver_chat_id=700,
        receiver_message_id=7002,
        source_chat_id=-1007001,
        source_message_id=7002,
        source_message_revision=0,
        source_origin_type="CHANNEL",
        source_message_timestamp=SOURCE_TIME + timedelta(minutes=5),
        raw_text="#ETHUSDT LONG Entry 200 Futures",
        content_hash="c" * 64,
        validation_status="STAGED",
        metadata_json={"event_kind": "INITIAL_SIGNAL"},
    )
    db_session.add(second)
    db_session.flush()
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=second)
    projection = HistoricalSemanticMaterializationService().materialize_revision(
        db_session,
        revision_id=revision.id,
    )
    batch.total_records = 2
    batch.accepted_records = 2
    db_session.flush()
    assert projection["status"] == "INCOMPLETE"

    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles(high=111)),
    )

    assert result["progressed"] == 1
    assert result["review_required"] == 1
    assert batch.status == "STAGING"
    assert second.validation_status == "STAGED"
    assert len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1
    assert result["items"][1]["status"] == "REVIEW_REQUIRED"
    assert result["items"][1]["reason"] == "AUTO_PROGRESS_BLOCKED:SEMANTIC_REVIEW_REQUIRED"
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []
    assert db_session.execute(select(PublicationDelivery)).scalars().all() == []


def test_auto_policy_never_sets_human_reviewer_identity(db_session):
    batch, _, revision, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )
    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles(high=111)),
    )
    draft = db_session.execute(
        select(HistoricalRecommendationDraft).where(
            HistoricalRecommendationDraft.revision_id == revision.id
        )
    ).scalar_one()
    assert result["status"] == "COMPLETED_UNVERIFIABLE"
    assert draft.reviewed_by_user_id is None
    assert draft.override_json["actor_type"] == "SYSTEM_POLICY"
    assert draft.override_json["human_reviewer"] is False
    assert draft.override_json["live_activation"] is False
