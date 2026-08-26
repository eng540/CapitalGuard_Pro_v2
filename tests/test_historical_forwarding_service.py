from datetime import datetime, timezone

from types import SimpleNamespace

from sqlalchemy import func, select

from capitalguard.application.services.historical_forwarding_service import (
    ForwardedMessageInput,
    HistoricalForwardingService,
)
from capitalguard.infrastructure.db.models import ChannelCatalog, PublicationDelivery, Recommendation, UserTrade
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.domain.entities import UserType


def _ts(hour: int) -> datetime:
    return datetime(2025, 1, 1, hour, 0, tzinfo=timezone.utc)


def test_forwarding_batch_stages_dedups_reviews_and_ingests_without_live_trade(db_session):
    reviewer = UserRepository(db_session).find_or_create(
        telegram_id=930001,
        user_type=UserType.ANALYST,
        first_name="Forward Reviewer",
    )
    catalog = ChannelCatalog(
        telegram_channel_id=-1009001,
        channel_code="CH-FWD-01",
        public_ref="CH-FWD-01",
        title="Forwarding Shadow Channel",
    )
    db_session.add(catalog)
    db_session.flush()

    service = HistoricalForwardingService()
    batch = service.start_batch(
        db_session,
        channel_catalog_id=catalog.id,
        requested_by_user_id=reviewer.id,
        expected_source_chat_id=-1009001,
        mode="BATCH",
        max_records=10,
    )
    valid = ForwardedMessageInput(
        receiver_chat_id=reviewer.telegram_user_id,
        receiver_message_id=100,
        forwarding_user_id=reviewer.id,
        source_chat_id=-1009001,
        source_message_id=77,
        source_origin_type="CHANNEL",
        source_message_timestamp=_ts(10),
        source_reply_to_message_id=None,
        raw_text="#BTCUSDT LONG Entry 100 SL 95 TP1 105@50% TP2 110@50%",
        metadata={"source_title": "Forwarding Shadow Channel"},
    )
    first = service.stage_message(db_session, batch_id=batch.id, message=valid)
    duplicate_receiver = service.stage_message(db_session, batch_id=batch.id, message=valid)
    duplicate_source = service.stage_message(
        db_session,
        batch_id=batch.id,
        message=ForwardedMessageInput(
            **{**valid.__dict__, "receiver_message_id": 101}
        ),
    )
    rejected_channel = service.stage_message(
        db_session,
        batch_id=batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=reviewer.telegram_user_id,
            receiver_message_id=102,
            forwarding_user_id=reviewer.id,
            source_chat_id=-1009002,
            source_message_id=78,
            source_origin_type="CHANNEL",
            source_message_timestamp=_ts(11),
            raw_text="#ETHUSDT LONG Entry 100",
        ),
    )
    hidden_origin = service.stage_message(
        db_session,
        batch_id=batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=reviewer.telegram_user_id,
            receiver_message_id=103,
            forwarding_user_id=reviewer.id,
            source_chat_id=None,
            source_message_id=None,
            source_origin_type="HIDDEN_USER",
            source_message_timestamp=_ts(12),
            raw_text="BTCUSDT LONG Entry 100",
        ),
    )

    assert duplicate_receiver.id == first.id
    assert duplicate_source.id == first.id
    assert rejected_channel.validation_status == "REJECTED_CHANNEL"
    assert hidden_origin.validation_status == "REJECTED_ORIGIN"

    preview = service.preview_batch(db_session, batch_id=batch.id)
    assert preview.total_records == 3
    assert preview.accepted_records == 1
    assert preview.rejected_records == 2
    assert preview.hidden_origin_records == 1
    assert preview.manifest["source_kind"] == "TELEGRAM_FORWARD"
    assert preview.manifest["records"][0]["telegram_message_id"] == 77

    validated = service.validate_batch(
        db_session,
        batch_id=batch.id,
        owner_note="Approved demo forwarding source and channel ownership",
    )
    assert validated.status == "VALIDATED"
    evidence = service.ingest_validated_batch(db_session, batch_id=batch.id)
    assert len(evidence) == 1
    assert evidence[0].telegram_message_id == 77

    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0
    assert db_session.scalar(select(func.count()).select_from(UserTrade)) == 0
    assert db_session.scalar(select(func.count()).select_from(PublicationDelivery)) == 0


def test_forwarding_normalizes_chat_ids_and_allows_retry_in_new_batch(db_session):
    reviewer = UserRepository(db_session).find_or_create(
        telegram_id=930002,
        user_type=UserType.ANALYST,
        first_name="Forward Retry Reviewer",
    )
    catalog = ChannelCatalog(
        telegram_channel_id=-1009101,
        channel_code="CH-FWD-RETRY",
        public_ref="CH-FWD-RETRY",
        title="Forwarding Retry Channel",
    )
    db_session.add(catalog)
    db_session.flush()
    service = HistoricalForwardingService()

    wrong_batch = service.start_batch(
        db_session,
        channel_catalog_id=catalog.id,
        requested_by_user_id=reviewer.id,
        expected_source_chat_id="-1009102",
        mode="BATCH",
    )
    rejected = service.stage_message(
        db_session,
        batch_id=wrong_batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=reviewer.telegram_user_id,
            receiver_message_id=200,
            forwarding_user_id=reviewer.id,
            source_chat_id="-1009101",
            source_message_id=4448,
            source_origin_type="CHANNEL",
            source_message_timestamp=_ts(13),
            raw_text="#BTCUSDT LONG Entry 72076",
        ),
    )
    assert rejected.validation_status == "REJECTED_CHANNEL"

    retry_batch = service.start_batch(
        db_session,
        channel_catalog_id=catalog.id,
        requested_by_user_id=reviewer.id,
        expected_source_chat_id=-1009101,
        mode="BATCH",
    )
    staged = service.stage_message(
        db_session,
        batch_id=retry_batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=reviewer.telegram_user_id,
            receiver_message_id=201,
            forwarding_user_id=reviewer.id,
            source_chat_id="-1009101",
            source_message_id=4448,
            source_origin_type="CHANNEL",
            source_message_timestamp=_ts(13),
            raw_text="#BTCUSDT LONG Entry 72076",
        ),
    )

    assert staged.id != rejected.id
    assert staged.validation_status == "STAGED"
    assert staged.source_chat_id == -1009101
    preview = service.preview_batch(db_session, batch_id=retry_batch.id)
    assert preview.total_records == 1
    assert preview.accepted_records == 1
    assert preview.rejected_records == 0


def test_preview_decisions_are_explicit_audited_and_never_create_live_entities(db_session):
    requester = UserRepository(db_session).find_or_create(
        telegram_id=930003,
        user_type=UserType.ANALYST,
        first_name="Explicit Preview Requester",
    )
    catalog = ChannelCatalog(
        telegram_channel_id=-1009201,
        channel_code="CH-FWD-DECISION",
        public_ref="CH-FWD-DECISION",
        title="Decision Channel",
    )
    db_session.add(catalog)
    db_session.flush()
    service = HistoricalForwardingService()

    def prepared_batch(receiver_message_id: int):
        batch = service.start_batch(
            db_session,
            channel_catalog_id=catalog.id,
            requested_by_user_id=requester.id,
            expected_source_chat_id=catalog.telegram_channel_id,
        )
        service.stage_message(
            db_session,
            batch_id=batch.id,
            message=ForwardedMessageInput(
                receiver_chat_id=requester.telegram_user_id,
                receiver_message_id=receiver_message_id,
                forwarding_user_id=requester.id,
                source_chat_id=catalog.telegram_channel_id,
                source_message_id=receiver_message_id,
                source_origin_type="CHANNEL",
                source_message_timestamp=_ts(14),
                raw_text="#BTCUSDT LONG Entry 100 SL 95 TP1 105@100%",
            ),
        )
        service.preview_batch(db_session, batch_id=batch.id)
        batch.metadata_json = {
            **(batch.metadata_json or {}),
            "parser_preview": {
                "review_actions_by_mode": {
                    "HISTORICAL_RECONSTRUCTION": ["IMPORT_HISTORICAL", "TRACK_ONLY", "DISMISS"]
                }
            },
        }
        db_session.flush()
        return batch

    import_batch = prepared_batch(301)
    imported = service.apply_preview_decision(
        db_session,
        batch_id=import_batch.id,
        requested_by_user_id=requester.id,
        action="IMPORT_HISTORICAL",
    )
    assert imported.status == "REVIEW_REQUIRED"
    assert imported.metadata_json["preview_decision"]["action"] == "IMPORT_HISTORICAL"

    track_batch = prepared_batch(302)
    tracked = service.apply_preview_decision(
        db_session,
        batch_id=track_batch.id,
        requested_by_user_id=requester.id,
        action="TRACK_ONLY",
    )
    assert tracked.status == "TRACK_ONLY"

    dismiss_batch = prepared_batch(303)
    dismissed = service.apply_preview_decision(
        db_session,
        batch_id=dismiss_batch.id,
        requested_by_user_id=requester.id,
        action="DISMISS",
    )
    assert dismissed.status == "DISMISSED"

    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0
    assert db_session.scalar(select(func.count()).select_from(UserTrade)) == 0
    assert db_session.scalar(select(func.count()).select_from(PublicationDelivery)) == 0


def test_preview_orders_records_by_source_time_and_marks_explicit_reply_timeline(db_session):
    reviewer = UserRepository(db_session).find_or_create(
        telegram_id=930010,
        user_type=UserType.ANALYST,
        first_name="Timeline Reviewer",
    )
    catalog = ChannelCatalog(
        telegram_channel_id=-1009010,
        channel_code="CH-TL-01",
        public_ref="CH-TL-01",
        title="Timeline Channel",
    )
    db_session.add(catalog)
    db_session.flush()
    service = HistoricalForwardingService()
    batch = service.start_batch(
        db_session,
        channel_catalog_id=catalog.id,
        requested_by_user_id=reviewer.id,
        expected_source_chat_id=-1009010,
        mode="BATCH",
        max_records=10,
    )

    messages = [
        ForwardedMessageInput(
            receiver_chat_id=reviewer.telegram_user_id,
            receiver_message_id=301,
            forwarding_user_id=reviewer.id,
            source_chat_id=-1009010,
            source_message_id=30,
            source_origin_type="CHANNEL",
            source_message_timestamp=_ts(12),
            raw_text="TP1 hit for BTCUSDT",
            source_reply_to_message_id=10,
        ),
        ForwardedMessageInput(
            receiver_chat_id=reviewer.telegram_user_id,
            receiver_message_id=302,
            forwarding_user_id=reviewer.id,
            source_chat_id=-1009010,
            source_message_id=10,
            source_origin_type="CHANNEL",
            source_message_timestamp=_ts(10),
            raw_text="#BTCUSDT LONG Entry 100 SL 95 TP1 105",
        ),
        ForwardedMessageInput(
            receiver_chat_id=reviewer.telegram_user_id,
            receiver_message_id=303,
            forwarding_user_id=reviewer.id,
            source_chat_id=-1009010,
            source_message_id=20,
            source_origin_type="CHANNEL",
            source_message_timestamp=_ts(11),
            raw_text="BTCUSDT update",
        ),
    ]
    for message in messages:
        service.stage_message(db_session, batch_id=batch.id, message=message)

    preview = service.preview_batch(db_session, batch_id=batch.id)
    records = preview.manifest["records"]

    assert [record["telegram_message_id"] for record in records] == [10, 20, 30]
    assert records[0]["metadata"]["timeline_role"] == "ROOT_CANDIDATE"
    assert records[1]["metadata"]["timeline_role"] == "ROOT_CANDIDATE"
    assert records[2]["metadata"]["timeline_role"] == "CHILD_UPDATE"
    assert records[2]["metadata"]["timeline_parent_message_id"] == 10
    assert records[2]["metadata"]["timeline_link_status"] == "EXPLICIT_REPLY"
    assert preview.manifest["ordering"] == "source_chat_id,source_message_timestamp,source_message_id,source_message_revision"


def test_source_order_key_uses_revision_as_final_tie_breaker():
    timestamp = _ts(10)
    base = dict(
        receiver_chat_id=500,
        receiver_message_id=1,
        forwarding_user_id=99,
        source_chat_id=-1009001,
        source_message_id=77,
        source_origin_type="CHANNEL",
        source_message_timestamp=timestamp,
        raw_text="BTCUSDT LONG",
    )
    older = ForwardedMessageInput(**base, source_message_revision=0)
    newer = ForwardedMessageInput(**base, source_message_revision=1)

    assert HistoricalForwardingService.canonical_order_key(older) < HistoricalForwardingService.canonical_order_key(newer)


def test_unresolved_reply_remains_pending_review_instead_of_being_merged():
    receipt = SimpleNamespace(
        id=1,
        source_chat_id=-1009001,
        source_message_id=88,
        source_message_timestamp=_ts(10),
        source_message_revision=0,
        source_reply_to_message_id=999,
    )

    annotations = HistoricalForwardingService._timeline_annotations([receipt])

    assert annotations[1]["timeline_role"] == "UNRESOLVED_CHILD"
    assert annotations[1]["timeline_link_status"] == "PENDING_REVIEW"
    assert annotations[1]["timeline_parent_message_id"] == 999
