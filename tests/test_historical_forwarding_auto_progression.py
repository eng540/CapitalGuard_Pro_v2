from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from capitalguard.application.services.historical_forwarding_service import ForwardedMessageInput, HistoricalForwardingService
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
        self.candles = list(candles or []); self.error = error; self.calls = 0
    def fetch(self, **kwargs):
        self.calls += 1
        if self.error: raise self.error
        return list(self.candles), "fake://historical-ohlcv"

def _candles(*, high=101, low=99):
    return [MarketCandle(asset="BTCUSDT", market="Futures", open_time=SOURCE_TIME + timedelta(minutes=5 * index), open=100, high=high, low=low, close=100, volume=1, data_source="FAKE") for index in range(3)]

def _auto_batch(db_session, *, raw_text, claim_status="CANONICAL", source_time=SOURCE_TIME):
    catalog = ChannelCatalog(telegram_channel_id=-1007001, channel_code="AUTO-PROGRESSION", public_ref="AUTO-PROGRESSION", title="Canonical historical source", is_active=True)
    db_session.add(catalog); db_session.flush()
    batch = HistoricalSignalService().create_import_batch(db_session, source_kind="TELEGRAM_FORWARD", manifest=[{"message_id": 7001}], channel_catalog_id=catalog.id, metadata={"mode": "AUTO", "source_chat_id": catalog.telegram_channel_id, "claim_status": claim_status, "canonical_channel_catalog_id": catalog.id, "discovery_source": "DIRECT_FORWARD"})
    batch.status = "STAGING"; batch.total_records = 1; batch.accepted_records = 1
    receipt = HistoricalForwardReceipt(batch_id=batch.id, forwarding_user_id=77, receiver_chat_id=700, receiver_message_id=7001, source_chat_id=catalog.telegram_channel_id, source_message_id=7001, source_message_revision=0, source_origin_type="CHANNEL", source_message_timestamp=source_time, raw_text=raw_text, content_hash="b" * 64, validation_status="STAGED", metadata_json={"event_kind": "INITIAL_SIGNAL"})
    db_session.add(receipt); db_session.flush()
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    projection = HistoricalSemanticMaterializationService().materialize_revision(db_session, revision_id=revision.id)
    db_session.flush(); return batch, receipt, revision, projection

def test_auto_progression_recovers_reused_revision_without_receipt_binding(db_session):
    batch, receipt, revision, projection = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    revision.receipt_id = receipt.id + 99999; db_session.flush()
    result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(_candles(high=111)))
    assert projection["status"] == "SUCCESS" and result["progressed"] == 1 and result["items"][0]["replay_status"] == "COMPLETED"

def test_canonical_complete_auto_progression_materializes_and_replays_without_live_entities(db_session):
    batch, receipt, revision, projection = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    assert projection["status"] == "SUCCESS"
    preview = HistoricalForwardingService().preview_batch(db_session, batch_id=batch.id)
    assert preview.manifest["records"][0]["metadata"]["source_message_timestamp"] == SOURCE_TIME.isoformat()
    provider = FakeProvider(_candles(high=111)); result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=provider)
    assert result["progressed"] == 1 and result["failed"] == 0 and result["review_required"] == 0 and result["status"] == "COMPLETED"
    item = result["items"][0]; assert item["status"] == "REPLAYED" and item["replay_status"] == "COMPLETED" and item["lifecycle_status"] == "CLOSED_TARGETS" and item["source_timestamp"] == SOURCE_TIME.isoformat()
    assert provider.calls == 1 and batch.status == "EVIDENCE_INGESTED" and receipt.validation_status == "INGESTED" and receipt.evidence_id is not None and revision.evidence_id == receipt.evidence_id
    assert len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1 and len(db_session.execute(select(HistoricalReplayRun)).scalars().all()) == 1
    assert db_session.execute(select(Recommendation)).scalars().all() == [] and db_session.execute(select(UserTrade)).scalars().all() == [] and db_session.execute(select(PublicationDelivery)).scalars().all() == []

def test_unclaimed_source_is_replayable_without_becoming_trusted_or_live(db_session):
    batch, receipt, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures", claim_status="UNCLAIMED")
    result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(_candles()))
    assert result["status"] == "PARTIAL" and result["progressed"] == 1 and result["failed"] == 1
    assert result["items"][0]["replay_status"] == "REPLAY_PARTIAL"
    assert receipt.validation_status == "INGESTED" and len(db_session.execute(select(HistoricalSignalEvidence)).scalars().all()) == 1
    evidence = db_session.execute(select(HistoricalSignalEvidence)).scalars().all()[0]; assert evidence.metadata_json["source_trust"] == "UNVERIFIED_FORWARD"
    signal = db_session.execute(select(HistoricalSignal)).scalars().all()[0]; assert signal.trust_tier == "UNVERIFIED" and signal.eligible_for_ranking is False

def test_incomplete_semantic_projection_stays_review_required(db_session):
    batch, receipt, _, projection = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100")
    assert projection["status"] == "INCOMPLETE"
    result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(_candles()))
    assert result["progressed"] == 0 and result["review_required"] == 1 and result["items"][0]["status"] == "REVIEW_REQUIRED" and receipt.validation_status == "STAGED" and db_session.execute(select(HistoricalSignal)).scalars().all() == []

def test_provider_failure_preserves_evidence_and_g5_and_is_safe_to_retry(db_session):
    batch, receipt, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    service = HistoricalForwardingService(); first = service.auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(error=RuntimeError("provider timeout")))
    assert first["progressed"] == 1 and first["failed"] == 1 and first["items"][0]["status"] == "REPLAY_FAILED" and "G5 evidence was preserved" in first["items"][0]["reason"]
    assert receipt.validation_status == "INGESTED" and len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1 and db_session.execute(select(HistoricalReplayRun)).scalars().all() == []
    second = service.auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(_candles()))
    assert second["items"][0]["status"] == "REPLAY_FAILED" and second["failed"] == 1

def test_repeated_successful_job_is_idempotent(db_session):
    batch, _, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    service = HistoricalForwardingService(); provider = FakeProvider(_candles()); kwargs = {"batch_id": batch.id, "replay_end": SOURCE_TIME + timedelta(minutes=10), "limit": 3, "provider": provider}
    first = service.auto_progress_canonical_batch(db_session, **kwargs); second = service.auto_progress_canonical_batch(db_session, **kwargs)
    assert first["status"] == second["status"] == "PARTIAL" and second["progressed"] == first["progressed"] == 1 and provider.calls == 1
    assert len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1 and len(db_session.execute(select(HistoricalReplayRun)).scalars().all()) == 1

def test_multiple_targets_are_preserved_and_partial_target_does_not_close_lifecycle(db_session):
    batch, _, _, projection = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 105@50% TP2 110@50% Futures")
    assert projection["status"] == "SUCCESS"
    result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(_candles(high=106)))
    assert result["progressed"] == 1; signal = db_session.execute(select(HistoricalSignal)).scalar_one(); assert len(signal.targets) == 2 and result["items"][0]["lifecycle_status"] == "ACTIVE_POSITION"

def test_ambiguous_candle_is_unverifiable_and_not_final(db_session):
    batch, _, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(_candles(high=110, low=90)))
    item = result["items"][0]; assert item["replay_status"] == "COMPLETED_UNVERIFIABLE" and item["lifecycle_status"] == "CLOSED_UNVERIFIABLE" and item["status"] == "REPLAYED" and result["status"] == "COMPLETED_UNVERIFIABLE"

def test_replay_window_shortfall_is_explicit_and_reaches_canonical_g6(db_session):
    old_time = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    batch, _, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures", source_time=old_time)
    provider = FakeProvider([MarketCandle(asset="BTCUSDT", market="Futures", open_time=old_time, open=100, high=101, low=99, close=100, volume=1, data_source="FAKE"), MarketCandle(asset="BTCUSDT", market="Futures", open_time=old_time + timedelta(minutes=15), open=100, high=101, low=99, close=100, volume=1, data_source="FAKE")])
    result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=old_time + timedelta(days=9), limit=2, provider=provider)
    item = result["items"][0]; assert result["progressed"] == 1 and item["status"] == "REPLAY_PARTIAL" and item["coverage_status"] == "PARTIAL_WINDOW" and item["replay_status"] == "REPLAY_PARTIAL" and provider.calls == 1
    runs = db_session.execute(select(HistoricalReplayRun)).scalars().all(); assert len(runs) == 1 and runs[0].status == "REPLAY_PARTIAL" and runs[0].coverage_status == "PARTIAL_WINDOW" and len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1

def test_mixed_batch_progresses_eligible_item_and_keeps_incomplete_item_staged(db_session):
    batch, _, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    second = HistoricalForwardReceipt(batch_id=batch.id, forwarding_user_id=77, receiver_chat_id=700, receiver_message_id=7002, source_chat_id=-1007001, source_message_id=7002, source_message_revision=0, source_origin_type="CHANNEL", source_message_timestamp=SOURCE_TIME + timedelta(minutes=5), raw_text="#ETHUSDT LONG Entry 200 Futures", content_hash="c" * 64, validation_status="STAGED", metadata_json={"event_kind": "INITIAL_SIGNAL"})
    db_session.add(second); db_session.flush(); revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=second); projection = HistoricalSemanticMaterializationService().materialize_revision(db_session, revision_id=revision.id); batch.total_records = 2; batch.accepted_records = 2; db_session.flush(); assert projection["status"] == "INCOMPLETE"
    result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(_candles(high=111)))
    assert result["progressed"] == 1 and result["review_required"] == 1 and batch.status == "STAGING" and second.validation_status == "STAGED" and len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1
    assert result["items"][1]["status"] == "REVIEW_REQUIRED" and result["items"][1]["reason"] == "AUTO_PROGRESS_BLOCKED:SEMANTIC_REVIEW_REQUIRED"
    assert db_session.execute(select(Recommendation)).scalars().all() == [] and db_session.execute(select(UserTrade)).scalars().all() == [] and db_session.execute(select(PublicationDelivery)).scalars().all() == []

def test_auto_policy_never_sets_human_reviewer_identity(db_session):
    batch, _, revision, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    result = HistoricalForwardingService().auto_progress_canonical_batch(db_session, batch_id=batch.id, replay_end=SOURCE_TIME + timedelta(minutes=10), limit=3, provider=FakeProvider(_candles(high=111)))
    draft = db_session.execute(select(HistoricalRecommendationDraft).where(HistoricalRecommendationDraft.revision_id == revision.id)).scalar_one()
    assert result["status"] == "COMPLETED" and draft.reviewed_by_user_id is None and draft.override_json["actor_type"] == "SYSTEM_POLICY" and draft.override_json["human_reviewer"] is False and draft.override_json["live_activation"] is False


def test_same_source_message_id_is_duplicate_and_rehydrates_final_replay(db_session):
    batch, _, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    first = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles(high=111)),
    )
    assert first["status"] == "COMPLETED"

    duplicate_batch = HistoricalForwardingService().start_batch(
        db_session,
        channel_catalog_id=batch.channel_catalog_id,
        requested_by_user_id=77,
        expected_source_chat_id=-1007001,
        mode="SINGLE",
        max_records=1,
    )
    duplicate = HistoricalForwardingService().stage_message(
        db_session,
        batch_id=duplicate_batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=701,
            receiver_message_id=8001,
            forwarding_user_id=77,
            source_chat_id=-1007001,
            source_message_id=7001,
            source_origin_type="CHANNEL",
            source_message_timestamp=SOURCE_TIME,
            raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
            metadata={"source_title": "Canonical historical source"},
        ),
    )
    assert duplicate.validation_status == "DUPLICATE"
    resolution = duplicate.metadata_json["duplicate_resolution"]
    assert resolution["status"] == "ALREADY_REGISTERED"
    assert resolution["replay"]["replay_status"] == "COMPLETED"
    assert resolution["replay"]["lifecycle_status"] == "CLOSED_TARGETS"

    preview = HistoricalForwardingService().preview_batch(db_session, batch_id=duplicate_batch.id)
    assert preview.accepted_records == 0
    assert preview.duplicate_records == 1
    assert preview.manifest["records"][0]["metadata"]["validation_status"] == "DUPLICATE"

    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=duplicate_batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles(high=111)),
    )
    assert result["status"] == "ALREADY_REGISTERED"
    assert result["progressed"] == 0
    assert result["duplicate_count"] == 1
    assert result["items"][0]["status"] == "ALREADY_REGISTERED"
    assert result["items"][0]["replay_status"] == "COMPLETED"


def test_same_content_with_different_source_message_id_is_new_recommendation(db_session):
    batch, _, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    service = HistoricalForwardingService()
    new_batch = service.start_batch(
        db_session,
        channel_catalog_id=batch.channel_catalog_id,
        requested_by_user_id=77,
        expected_source_chat_id=-1007001,
        mode="SINGLE",
        max_records=1,
    )
    receipt = service.stage_message(
        db_session,
        batch_id=new_batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=701,
            receiver_message_id=8002,
            forwarding_user_id=77,
            source_chat_id=-1007001,
            source_message_id=7002,
            source_origin_type="CHANNEL",
            source_message_timestamp=SOURCE_TIME,
            raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
            metadata={"source_title": "Canonical historical source"},
        ),
    )
    assert receipt.validation_status == "STAGED"
    preview = service.preview_batch(db_session, batch_id=new_batch.id)
    assert preview.accepted_records == 1
    assert preview.duplicate_records == 0
    assert preview.manifest["records"][0]["telegram_message_id"] == 7002


def test_same_source_message_id_is_duplicate_and_rehydrates_final_replay(db_session):
    batch, _, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    first = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles(high=111)),
    )
    assert first["status"] == "COMPLETED"

    duplicate_batch = HistoricalForwardingService().start_batch(
        db_session,
        channel_catalog_id=batch.channel_catalog_id,
        requested_by_user_id=77,
        expected_source_chat_id=-1007001,
        mode="SINGLE",
        max_records=1,
    )
    duplicate = HistoricalForwardingService().stage_message(
        db_session,
        batch_id=duplicate_batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=701,
            receiver_message_id=8001,
            forwarding_user_id=77,
            source_chat_id=-1007001,
            source_message_id=7001,
            source_origin_type="CHANNEL",
            source_message_timestamp=SOURCE_TIME,
            raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
            metadata={"source_title": "Canonical historical source"},
        ),
    )
    assert duplicate.validation_status == "DUPLICATE"
    resolution = duplicate.metadata_json["duplicate_resolution"]
    assert resolution["status"] == "ALREADY_REGISTERED"
    assert resolution["replay"]["replay_status"] == "COMPLETED"
    assert resolution["replay"]["lifecycle_status"] == "CLOSED_TARGETS"

    preview = HistoricalForwardingService().preview_batch(db_session, batch_id=duplicate_batch.id)
    assert preview.accepted_records == 0
    assert preview.duplicate_records == 1
    assert preview.manifest["records"][0]["metadata"]["validation_status"] == "DUPLICATE"

    result = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=duplicate_batch.id,
        replay_end=SOURCE_TIME + timedelta(minutes=10),
        limit=3,
        provider=FakeProvider(_candles(high=111)),
    )
    assert result["status"] == "ALREADY_REGISTERED"
    assert result["progressed"] == 0
    assert result["duplicate_count"] == 1
    assert result["items"][0]["status"] == "ALREADY_REGISTERED"
    assert result["items"][0]["replay_status"] == "COMPLETED"


def test_same_content_with_different_source_message_id_is_new_recommendation(db_session):
    batch, _, _, _ = _auto_batch(db_session, raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures")
    service = HistoricalForwardingService()
    new_batch = service.start_batch(
        db_session,
        channel_catalog_id=batch.channel_catalog_id,
        requested_by_user_id=77,
        expected_source_chat_id=-1007001,
        mode="SINGLE",
        max_records=1,
    )
    receipt = service.stage_message(
        db_session,
        batch_id=new_batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=701,
            receiver_message_id=8002,
            forwarding_user_id=77,
            source_chat_id=-1007001,
            source_message_id=7002,
            source_origin_type="CHANNEL",
            source_message_timestamp=SOURCE_TIME,
            raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
            metadata={"source_title": "Canonical historical source"},
        ),
    )
    assert receipt.validation_status == "STAGED"
    preview = service.preview_batch(db_session, batch_id=new_batch.id)
    assert preview.accepted_records == 1
    assert preview.duplicate_records == 0
    assert preview.manifest["records"][0]["telegram_message_id"] == 7002

