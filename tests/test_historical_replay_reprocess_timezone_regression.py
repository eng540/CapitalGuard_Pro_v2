from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from capitalguard.application.services.historical_forwarding_service import (
    ForwardedMessageInput,
    HistoricalForwardingService,
)
from capitalguard.application.services.historical_message_foundation_service import (
    HistoricalMessageFoundationService,
)
from capitalguard.application.services.historical_semantic_materialization_service import (
    HistoricalSemanticMaterializationService,
)
from capitalguard.application.services.historical_signal_service import HistoricalSignalService
from capitalguard.application.services.historical_market_replay_service import MarketCandle
from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    HistoricalForwardReceipt,
    HistoricalReplayRun,
    HistoricalSignal,
    HistoricalSignalMaterialization,
)

SOURCE_TIME = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, candles=None, error=None):
        self.candles = list(candles or [])
        self.error = error

    def fetch(self, **kwargs):
        if self.error:
            raise self.error
        return list(self.candles), "fake://historical-ohlcv"


def _auto_batch(db_session):
    catalog = ChannelCatalog(
        telegram_channel_id=-1007001,
        channel_code="REPROCESS-TIMEZONE",
        public_ref="REPROCESS-TIMEZONE",
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
            "claim_status": "UNCLAIMED",
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
        source_message_timestamp=SOURCE_TIME,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
        content_hash="b" * 64,
        validation_status="STAGED",
        metadata_json={"event_kind": "INITIAL_SIGNAL"},
    )
    db_session.add(receipt)
    db_session.flush()

    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    projection = HistoricalSemanticMaterializationService().materialize_revision(
        db_session, revision_id=revision.id
    )
    db_session.flush()
    assert projection["status"] == "SUCCESS"
    return batch, receipt


def _partial_provider():
    return FakeProvider(
        [
            MarketCandle(
                asset="BTCUSDT",
                market="Futures",
                open_time=SOURCE_TIME,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1,
                data_source="FAKE",
            )
        ]
    )


def test_partial_duplicate_reprocess_restores_timezone_and_creates_lineage(db_session):
    batch, _ = _auto_batch(db_session)
    service = HistoricalForwardingService()

    first = service.auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(days=9),
        limit=1,
        provider=_partial_provider(),
    )
    assert first["progressed"] == 1
    assert first["failed"] == 0
    assert first["partial"] == 1
    assert first["status"] == "PARTIAL"

    signal = db_session.execute(select(HistoricalSignal)).scalar_one()
    signal_id = signal.id
    materialization = db_session.execute(
        select(HistoricalSignalMaterialization).where(
            HistoricalSignalMaterialization.signal_id == signal_id
        )
    ).scalar_one()
    materialization_id = materialization.id

    first_run = db_session.execute(select(HistoricalReplayRun)).scalar_one()
    assert first_run.status == "REPLAY_PARTIAL"
    assert first_run.reprocess_of_run_id is None

    # Force a real ORM reload: SQLite returns DateTime(timezone=True) values as naive.
    db_session.expunge(signal)
    reloaded_signal = db_session.get(HistoricalSignal, signal_id)
    assert reloaded_signal is not None
    assert reloaded_signal.decision_timestamp.tzinfo is not None
    assert reloaded_signal.decision_timestamp.utcoffset() == timedelta(0)
    assert reloaded_signal.eligible_for_ranking is False

    duplicate_batch = service.start_batch(
        db_session,
        channel_catalog_id=batch.channel_catalog_id,
        requested_by_user_id=77,
        expected_source_chat_id=-1007001,
        mode="SINGLE",
        max_records=1,
    )
    duplicate = service.stage_message(
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

    second = service.auto_progress_canonical_batch(
        db_session,
        batch_id=duplicate_batch.id,
        replay_end=SOURCE_TIME + timedelta(days=9),
        limit=1,
        provider=_partial_provider(),
    )

    assert second["progressed"] == 0
    assert second["failed"] == 0
    assert second["partial"] == 1
    assert second["status"] == "PARTIAL"
    assert second["duplicate_count"] == 1
    assert second["items"][0]["status"] == "ALREADY_REGISTERED"
    assert second["items"][0]["replay_status"] == "REPLAY_PARTIAL"

    runs = db_session.execute(
        select(HistoricalReplayRun).order_by(HistoricalReplayRun.id)
    ).scalars().all()
    assert len(runs) == 2

    second_run = runs[1]
    assert second_run.id != first_run.id
    assert second_run.status == "REPLAY_PARTIAL"
    assert second_run.reprocess_of_run_id == first_run.id
    assert second_run.signal_id == signal_id
    assert second_run.materialization_id == materialization_id
    assert second_run.request_fingerprint != first_run.request_fingerprint

    assert len(db_session.execute(select(HistoricalSignal)).scalars().all()) == 1
    reloaded_signal = db_session.get(HistoricalSignal, signal_id)
    assert reloaded_signal.decision_timestamp.tzinfo is not None
    assert reloaded_signal.eligible_for_ranking is False
